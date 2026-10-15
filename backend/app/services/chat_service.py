"""
ChatService — shared logic for chat SSE streaming.

Extracts duplicated code from chat.py's two SSE generators:
- Response text extraction from structured agent results
- Reasoning/thinking extraction from agent messages
- Fire-and-forget episodic memory storage
- Chat session persistence
"""

import asyncio
import logging
import uuid
from typing import Any, List, Optional

from sqlalchemy import select

from app.core.database import AsyncSessionLocal, set_tenant_context
from app.models.chat_session import ChatSession
from app.services.chat_backfill import append_message_row, ensure_backfilled

logger = logging.getLogger(__name__)

_task_semaphore = asyncio.Semaphore(20)  # Max 20 concurrent background tasks


def bounded_create_task(coro):
    """Create a bounded background task. Drops task if semaphore is full."""
    if _task_semaphore.locked():
        logger.warning("Background task queue full, dropping task")
        coro.close()  # Prevent "coroutine was never awaited" warning
        return None

    async def _bounded():
        async with _task_semaphore:
            try:
                await coro
            except Exception as e:
                logger.error("Background task failed: %s", e)

    return asyncio.create_task(_bounded())


def extract_reasoning(result: Any) -> Optional[str]:
    """Extract chain-of-thought reasoning from an agent result."""
    if hasattr(result, '_reasoning'):
        return result._reasoning
    if hasattr(result, 'all_messages'):
        for msg in result.all_messages():
            if hasattr(msg, 'parts'):
                for part in msg.parts:
                    if hasattr(part, 'content') and part.__class__.__name__ == 'ThinkingPart':
                        return part.content
    return None


def extract_response_text(final_data: Any) -> str:
    """
    Extract human-readable text from a structured agent result.

    Handles: GeneralResponse, ResearchResult, AnalysisResult,
    ComplianceResult, DataRetrievalResult, and fallback to str().
    """
    # GeneralResponse — simple conversational
    if hasattr(final_data, 'response') and not hasattr(final_data, 'analysis'):
        return final_data.response

    # ResearchResult — has answer and sources
    if hasattr(final_data, 'answer'):
        text = final_data.answer
        if hasattr(final_data, 'sources') and final_data.sources:
            text += f"\n\nSources: {', '.join(final_data.sources)}"
        return text

    if hasattr(final_data, 'response'):
        return final_data.response

    if hasattr(final_data, 'analysis'):
        return final_data.analysis

    if hasattr(final_data, 'summary'):
        return final_data.summary

    # AnalysisResult — reasoning is a list of strings
    if hasattr(final_data, 'reasoning') and isinstance(final_data.reasoning, list):
        parts = []
        if hasattr(final_data, 'recommendation'):
            parts.append(f"**Recommendation:** {final_data.recommendation.upper()}")
        if hasattr(final_data, 'confidence'):
            parts.append(f"**Confidence:** {final_data.confidence:.0%}")
        if final_data.reasoning:
            parts.append("\n**Analysis:**\n" + "\n".join(f"- {r}" for r in final_data.reasoning))
        if hasattr(final_data, 'key_metrics') and final_data.key_metrics:
            metrics = ", ".join(f"{k}: {v}" for k, v in final_data.key_metrics.items())
            parts.append(f"\n**Key Metrics:** {metrics}")
        if hasattr(final_data, 'risks') and final_data.risks:
            parts.append("\n**Risks:**\n" + "\n".join(f"- {r}" for r in final_data.risks))
        return "\n".join(parts) if parts else str(final_data)

    # ComplianceResult
    if hasattr(final_data, 'is_compliant'):
        parts = []
        status = "Compliant" if final_data.is_compliant else "Non-Compliant"
        parts.append(f"**Status:** {status}")
        if hasattr(final_data, 'risk_level'):
            parts.append(f"**Risk Level:** {final_data.risk_level.upper()}")
        if hasattr(final_data, 'violations') and final_data.violations:
            parts.append("\n**Violations:**\n" + "\n".join(f"- {v}" for v in final_data.violations))
        if hasattr(final_data, 'regulations_checked') and final_data.regulations_checked:
            parts.append("\n**Regulations Checked:**\n" + "\n".join(f"- {r}" for r in final_data.regulations_checked))
        if hasattr(final_data, 'recommendations') and final_data.recommendations:
            parts.append("\n**Recommendations:**\n" + "\n".join(f"- {r}" for r in final_data.recommendations))
        return "\n".join(parts) if parts else str(final_data)

    # DataRetrievalResult
    if hasattr(final_data, 'success') and hasattr(final_data, 'data'):
        parts = []
        status = "Success" if final_data.success else "Failed"
        parts.append(f"**Status:** {status}")
        if final_data.error:
            parts.append(f"\n**Error:** {final_data.error}")
        if final_data.data:
            parts.append("\n**Data Retrieved:**")
            for key, value in final_data.data.items():
                parts.append(f"\n**{key}:** {value}")
        if hasattr(final_data, 'sources') and final_data.sources:
            parts.append("\n**Sources:**\n" + "\n".join(f"- {s}" for s in final_data.sources))
        return "\n".join(parts) if parts else str(final_data)

    return str(final_data)


def store_memory_async(
    agent: Any,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    message_content: str,
    response_text: str,
    session_id: Optional[uuid.UUID],
    reasoning: Optional[str],
) -> None:
    """Fire-and-forget episodic memory storage after a chat response."""
    if not (hasattr(agent, 'store_decision_to_memory') and response_text):
        return

    async def _store():
        try:
            async with AsyncSessionLocal() as db:
                if tenant_id:
                    await set_tenant_context(db, str(tenant_id))
                await agent.store_decision_to_memory(
                    db=db,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    context=message_content,
                    decision=response_text[:500],
                    session_id=str(session_id) if session_id else None,
                    action_type="chat",
                    rationale=reasoning,
                )
        except Exception:
            pass

    bounded_create_task(_store())


async def persist_session(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: Optional[uuid.UUID],
    message_content: str,
    response_text: str,
    reasoning: Optional[str] = None,
    message_parts: Optional[List] = None,
    duration_ms: Optional[int] = None,
    model: str = "unknown",
    project_id: Optional[str] = None,
) -> uuid.UUID:
    """Persist user + assistant messages to a ChatSession. Returns session ID."""
    async with AsyncSessionLocal() as db:
        if tenant_id:
            await set_tenant_context(db, str(tenant_id))

        session = None
        if session_id:
            result = await db.execute(
                select(ChatSession).where(
                    ChatSession.id == session_id,
                    ChatSession.user_id == user_id,
                )
            )
            session = result.scalar_one_or_none()

        if not session:
            session = ChatSession(
                id=session_id or uuid.uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                context_type="agent",
                title=message_content[:50] + ("..." if len(message_content) > 50 else ""),
                messages=[],
                is_draft=False,
                project_id=uuid.UUID(project_id) if project_id else None,
            )
            db.add(session)
            await db.flush()

        # Dual-write (U10): bring any pre-existing history into the read model first, then project
        # each appended message in this same transaction (failure rolls back both stores).
        await ensure_backfilled(db, session)
        user_msg = await session.add_message(role="user", content=message_content, db=db)
        await append_message_row(db, session.tenant_id, session.id, user_msg)

        metadata = None
        if reasoning or message_parts or duration_ms:
            metadata = {}
            if reasoning:
                metadata["reasoning"] = reasoning
            if message_parts:
                metadata["parts"] = [
                    p.model_dump() if hasattr(p, 'model_dump') else p
                    for p in message_parts
                ]
            if duration_ms:
                metadata["duration_ms"] = duration_ms
            if model != "unknown":
                metadata["model"] = model

        assistant_msg = await session.add_message(
            role="assistant", content=response_text, metadata=metadata, db=db)
        await append_message_row(db, session.tenant_id, session.id, assistant_msg)
        await db.commit()

        # Log messages to filesystem session history
        _log_to_session_history(
            str(session.tenant_id), str(session.id), str(user_id),
            message_content, response_text,
        )

        return session.id


async def stored_history_messages(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    client_messages: List[dict],
) -> Optional[List[dict]]:
    """Server-side history guard for legacy ``POST /api/v1/chat`` (U10, ``CHAT_SERVER_HISTORY``).

    When ``session_id`` names a stored session owned by ``user_id``, returns the message list to
    build model history from: the stored messages (including tool-call parts) followed by the
    client's latest user message with only its text. Client-supplied assistant/tool messages and
    parts, and any client metadata, are discarded. Returns ``None`` (caller keeps the client
    messages) when the setting is off or the session does not exist.
    """
    from app.config import get_settings
    if not get_settings().CHAT_SERVER_HISTORY:
        return None

    async with AsyncSessionLocal() as db:
        if tenant_id:
            await set_tenant_context(db, str(tenant_id))
        result = await db.execute(
            select(ChatSession.messages).where(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id,
            )
        )
        row = result.first()
    if row is None:
        return None

    latest_user = next((m for m in reversed(client_messages) if m.get("role") == "user"), None)
    forwarded = [dict(m) for m in (row[0] or []) if isinstance(m, dict)]
    if latest_user is not None:
        forwarded.append({k: v for k, v in latest_user.items() if k != "metadata"})
    return forwarded


RECAP_TURNS = 6
_RECAP_CHARS = 600
_SUMMARY_INPUT_CHARS = 12000


def _transcript_text(transcript: List[dict]) -> List[tuple]:
    """(role, text) pairs from read-model-shaped messages, using text parts or ``content``."""
    out = []
    for m in transcript:
        if m.get("role") not in ("user", "assistant"):
            continue
        texts = [p.get("text", "") for p in (m.get("parts") or [])
                 if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str)]
        text = "\n".join(t for t in texts if t) or (m.get("content") or "")
        if text.strip():
            out.append((m["role"], text.strip()))
    return out


def deterministic_recap(transcript: List[dict], turns: int = RECAP_TURNS) -> str:
    """Last-N-turns text recap, used when the summary model call fails."""
    pairs = _transcript_text(transcript)[-turns * 2:]
    lines = [f"{'User' if r == 'user' else 'Assistant'}: {t[:_RECAP_CHARS]}" for r, t in pairs]
    return "Recap of the earlier conversation (most recent turns):\n\n" + "\n\n".join(lines)


async def build_continue_summary(
    db: Any, *, tenant_id: uuid.UUID, user_id: uuid.UUID, transcript: List[dict]
) -> tuple:
    """Summary that seeds a continued conversation (KTD8): ``(text, "model" | "recap")``.

    One call through the model policy's low tier; any failure (no provider, quota, timeout,
    empty answer) falls back to the deterministic last-N-turns recap.
    """
    pairs = _transcript_text(transcript)
    if not pairs:
        return deterministic_recap(transcript), "recap"
    convo = "\n\n".join(f"{r}: {t}" for r, t in pairs)[-_SUMMARY_INPUT_CHARS:]
    try:
        from app.services.llm_gateway import LLMGateway, LLMRequest, resolve_model_for_tier
        model = await resolve_model_for_tier(db, tenant_id, "low")
        resp = await LLMGateway(db).complete(
            LLMRequest(
                model=model, temperature=0.2, max_tokens=600,
                messages=[
                    {"role": "system", "content": (
                        "Summarize this earlier conversation so the assistant can continue it. "
                        "Keep goals, decisions, key facts, open questions and next steps. "
                        "Plain text, under 300 words.")},
                    {"role": "user", "content": convo},
                ]),
            tenant_id, user_id)
        text = (resp.content or "").strip()
        if text:
            return f"Summary of the earlier conversation:\n\n{text}", "model"
    except Exception as e:  # noqa: BLE001 - any failure degrades to the recap
        logger.warning("Continue summary failed, using recap: %s", e)
    return deterministic_recap(transcript), "recap"


async def set_engine_conversation_title(db: Any, conversation_id: uuid.UUID, first_user_text: str) -> None:
    """Set the list title from the first user entry (truncated); keeps an existing title."""
    from sqlalchemy import update
    from app.models.engine import EngineConversation
    text = " ".join((first_user_text or "").split())
    if not text:
        return
    title = text[:50] + ("..." if len(text) > 50 else "")
    await db.execute(
        update(EngineConversation)
        .where(EngineConversation.conversation_id == conversation_id, EngineConversation.title.is_(None))
        .values(title=title)
    )


async def touch_engine_conversation(db: Any, conversation_id: uuid.UUID) -> None:
    """Bump ``updated_at`` so the union list orders the conversation by activity."""
    from sqlalchemy import func, update
    from app.models.engine import EngineConversation
    await db.execute(
        update(EngineConversation)
        .where(EngineConversation.conversation_id == conversation_id)
        .values(updated_at=func.now())
    )


def _log_to_session_history(
    tenant_id: str, session_id: str, user_id: str,
    user_message: str, assistant_message: str,
) -> None:
    """Fire-and-forget: append user/assistant messages to session logs/history.md."""
    from app.config import get_settings
    settings = get_settings()
    if not settings.USE_SESSION_FILESYSTEM:
        return

    async def _write():
        try:
            from app.services.session_storage import get_session_storage
            from datetime import datetime, timezone
            storage = get_session_storage()
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

            entry = (
                f"\n---\n\n"
                f"**[{timestamp}] User:**\n{user_message[:500]}\n\n"
                f"**[{timestamp}] Assistant:**\n{assistant_message[:1000]}\n"
            )

            # Append to existing history or start fresh
            try:
                existing = await storage.read(tenant_id, session_id, "logs/history.md")
                content = existing + entry
            except (ValueError, FileNotFoundError):
                content = f"# Chat History\n\nSession: {session_id}\n" + entry

            await storage.write(tenant_id, session_id, "logs/history.md", content)
        except Exception:
            pass  # Non-critical — don't break chat flow

    bounded_create_task(_write())


def format_error(error_msg: str) -> str:
    """Sanitize error messages for client consumption.

    Maps known exceptions to user-friendly messages and defaults to
    a generic message to prevent leaking stack traces, file paths,
    or internal details to clients.
    """
    lower = error_msg.lower()

    if "401" in error_msg or "api_key" in lower:
        return "API key not configured. Add your key in Settings > API Keys."
    if "rate limit" in lower or "429" in error_msg:
        return "API rate limit exceeded. Please try again in a moment."
    if "timeout" in lower or "timed out" in lower:
        return "The request timed out. Please try again."
    if "connection" in lower and ("refused" in lower or "reset" in lower or "error" in lower):
        return "Could not connect to the service. Please try again later."
    if "permission" in lower or "403" in lower or "forbidden" in lower:
        return "You don't have permission to perform this action."
    if "not found" in lower or "404" in lower:
        return "The requested resource was not found."
    if "validation" in lower or "invalid" in lower:
        return "Invalid request. Please check your input and try again."
    if "context" in lower and ("length" in lower or "window" in lower or "too long" in lower):
        return "The conversation is too long. Please start a new chat."
    if "model" in lower and ("not available" in lower or "not found" in lower or "does not exist" in lower):
        return "The selected model is not available. Please choose a different model in Settings."
    if "content_filter" in lower or "content filter" in lower or "content_policy" in lower:
        return "The response was filtered due to content policy. Please rephrase your message."

    # Default: log full error server-side, return generic message to client
    logger.error("Unhandled error (sanitized for client): %s", error_msg)
    return "An error occurred while processing your request. Please try again."
