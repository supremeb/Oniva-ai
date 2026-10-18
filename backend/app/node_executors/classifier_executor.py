"""
Question Classifier Node Executor
Uses an LLM to classify input into categories.

Plan 003 U14: ``build_prompt`` and ``parse_answer`` are pure. ``prepare`` returns an ``llm`` body,
``finish`` maps the answer to ``selectedHandles = [label]``; ``execute`` is a thin legacy wrapper over
the same functions (until U30).
"""

import json
import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.node_executors import (
    BaseNodeExecutor,
    LlmKeyMissing,
    NodeExecutionContext,
    NodeExecutionResult,
    PrepareResult,
    legacy_complete,
)
from app.services.llm_gateway import normalize_model_id


class ClassificationResult(BaseModel):
    """Output from classifier"""
    class_id: str
    class_name: str
    confidence: float


class ClassifierExecutor(BaseNodeExecutor):
    """Executor for Question Classifier node - LLM-based classification"""

    node_type = "classifier"

    @staticmethod
    def _normalize_classes(classes: List[Any]) -> List[Dict[str, Any]]:
        """Handle both dict and string class formats."""
        normalized: List[Dict[str, Any]] = []
        for i, c in enumerate(classes):
            if isinstance(c, dict):
                normalized.append(c)
            elif isinstance(c, str):
                normalized.append({"id": c, "name": c})
            else:
                normalized.append({"id": f"class_{i}", "name": str(c)})
        return normalized

    def build_prompt(self, context: NodeExecutionContext) -> Dict[str, Any]:
        """Pure: the classification prompt, the label set and the resolved input text.

        Raises ``ValueError`` when the node has no classes.
        """
        node_data = context.node_data
        classes = node_data.get("classes", [])
        instructions = node_data.get("instructions", "")

        input_text = self.substitute_variables(node_data.get("inputText", ""), context.variables, context.inputs)
        if not input_text:
            input_text = context.variables.get("sys.query", "")

        if not classes:
            raise ValueError("No classes defined for classification")
        classes = self._normalize_classes(classes)

        class_list = "\n".join(
            f"- {c.get('id', f'class_{i}')}: {c.get('name', 'Unnamed')}" for i, c in enumerate(classes)
        )
        system_prompt = f"""You are a text classifier. Classify the input text into one of these categories:

{class_list}

{instructions if instructions else ''}

Respond with ONLY a JSON object in this format:
{{"class_id": "<id>", "class_name": "<name>", "confidence": <0.0-1.0>}}

Be precise and choose the most appropriate category."""

        return {
            "system": system_prompt,
            "prompt": f"Classify this text:\n\n{input_text}",
            "classes": classes,
            "labels": [c.get("id", f"class_{i}") for i, c in enumerate(classes)],
            "input_text": input_text,
            "model": normalize_model_id(node_data.get("model", "openai/gpt-4o-mini")),
        }

    @staticmethod
    def _default_class(classes: List[Dict[str, Any]], node_data: Dict[str, Any]) -> Dict[str, Any]:
        """The class an unknown label routes to: the configured default, else the first class."""
        wanted = node_data.get("defaultClass") or node_data.get("defaultClassId")
        for c in classes:
            if wanted and c.get("id") == wanted:
                return c
        return classes[0]

    def parse_answer(self, context: NodeExecutionContext, text: str, tokens: int = 0) -> NodeExecutionResult:
        """Pure: map the model's answer to a label. An unknown label routes to the default class."""
        built = self.build_prompt(context)
        classes, input_text = built["classes"], built["input_text"]
        known = {c.get("id", "") for c in classes}
        result_text = text or ""

        try:
            json_match = result_text
            if "```json" in result_text:
                json_match = result_text.split("```json")[1].split("```")[0]
            elif "```" in result_text:
                json_match = result_text.split("```")[1].split("```")[0]
            classification = json.loads(json_match.strip())
            if not isinstance(classification, dict):
                raise json.JSONDecodeError("not an object", json_match, 0)
        except (json.JSONDecodeError, IndexError):
            classification = None
            # Fallback: try to match a class name in the response
            lowered = result_text.lower()
            for c in classes:
                if c.get("name", "").lower() in lowered:
                    return self._result(
                        context, c, 0.7, input_text, tokens, raw_response=result_text, handle=c.get("id")
                    )
            default = self._default_class(classes, context.node_data)
            return self._result(
                context, default, 0.5, input_text, tokens, raw_response=result_text, handle=default.get("id")
            )

        label = classification.get("class_id", "")
        if label in known:
            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    "class_id": label,
                    "class_name": classification.get("class_name", ""),
                    "confidence": classification.get("confidence", 0.0),
                    "input_text": input_text,
                },
                tokens_used=tokens or None,
                next_node_id=label,
            )
        default = self._default_class(classes, context.node_data)
        return self._result(
            context, default, 0.5, input_text, tokens, raw_response=result_text, handle=default.get("id")
        )

    @staticmethod
    def _result(
        context: NodeExecutionContext,
        cls: Dict[str, Any],
        confidence: float,
        input_text: str,
        tokens: int,
        *,
        raw_response: str,
        handle: Optional[str],
    ) -> NodeExecutionResult:
        return NodeExecutionResult(
            node_id=context.node_id,
            status="success",
            output={
                "class_id": cls.get("id", ""),
                "class_name": cls.get("name", ""),
                "confidence": confidence,
                "input_text": input_text,
                "raw_response": raw_response,
            },
            tokens_used=tokens or None,
            next_node_id=handle or None,
        )

    async def prepare(self, context: NodeExecutionContext, db: AsyncSession) -> Any:
        try:
            built = self.build_prompt(context)
        except ValueError as exc:
            return NodeExecutionResult(node_id=context.node_id, status="failed", error=str(exc))
        return PrepareResult.llm(
            system=built["system"],
            prompt=built["prompt"],
            model=built["model"] if context.node_data.get("model") else None,
            tier="low",
            response_format="json",
        )

    async def finish(self, context: NodeExecutionContext, db: AsyncSession, finish_input: Any) -> NodeExecutionResult:
        return self.parse_answer(
            context, PrepareResult.answer_text(finish_input), PrepareResult.answer_tokens(finish_input)
        )

    async def execute(self, context: NodeExecutionContext, db: AsyncSession) -> NodeExecutionResult:
        """Legacy in-process path over the same pure functions (removed by U30)."""
        start_time = time.time()
        try:
            built = self.build_prompt(context)
            completion = await legacy_complete(
                db,
                context.tenant_id,
                built["model"],
                built["system"],
                built["prompt"],
                temperature=0.1,
                max_tokens=200,
                timeout=30.0,
            )
            result = self.parse_answer(context, completion["text"], completion["tokens"])
            result.elapsed_ms = int((time.time() - start_time) * 1000)
            return result
        except (ValueError, LlmKeyMissing) as e:
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=str(e),
                elapsed_ms=int((time.time() - start_time) * 1000),
            )
        except Exception as e:
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=str(e),
                elapsed_ms=int((time.time() - start_time) * 1000),
            )
