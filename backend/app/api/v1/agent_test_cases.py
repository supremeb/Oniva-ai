"""Agent Test Cases API endpoints for Agent Studio evaluation.

Manages test cases for agent accuracy measurement.

Note: Test cases are only supported for custom agents (UUID IDs).
Built-in agents (string IDs like 'data-retrieval') use predefined behavior
and don't support custom test cases.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import Optional
from uuid import UUID
from datetime import datetime
import json
import csv
import io

from app.models.agent_test_case import AgentTestCase
from app.core.database import get_db
from app.auth.dependencies import get_current_tenant_id
from app.api.dependencies import verify_agent_access, AgentInfo, parse_agent_id
from app.core.builtin_agents import is_builtin_agent_id
from app.schemas.agent_studio import (
    CreateTestCaseRequest,
    UpdateTestCaseRequest,
    TestCaseResponse,
    TestCaseListResponse,
    ImportTestCasesResult,
    TestCaseLastResult,
    GenerateTestsRequest,
    GenerateTestsResponse,
    GeneratedTestCase,
)
from app.auth.dependencies import get_current_user_id

router = APIRouter()


def _get_agent_uuid(agent_id: str) -> UUID:
    """
    Parse agent_id and return UUID if valid, raise 400 if built-in agent.
    Built-in agents don't support custom test cases.
    """
    # Reject built-in agents (accepts both slugs and UUIDs)
    if is_builtin_agent_id(agent_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Test cases are only supported for custom agents. Built-in agent '{agent_id}' uses predefined behavior."
        )
    agent_uuid, _ = parse_agent_id(agent_id)
    if agent_uuid is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid agent ID: '{agent_id}'. Expected a UUID."
        )
    return agent_uuid


def test_case_to_response(test_case: AgentTestCase) -> TestCaseResponse:
    """Convert AgentTestCase model to response schema."""
    last_result = None
    if test_case.last_result:
        last_result = TestCaseLastResult(
            passed=test_case.last_result.get('passed', False),
            actual_output=test_case.last_result.get('actual_output', ''),
            score=test_case.last_result.get('score'),
            evaluated_at=datetime.fromisoformat(test_case.last_result.get('evaluated_at')) if test_case.last_result and test_case.last_result.get('evaluated_at') else datetime.utcnow(),
            duration_ms=test_case.last_result.get('duration_ms'),
        )

    return TestCaseResponse(
        id=test_case.id,
        tenant_id=test_case.tenant_id,
        agent_id=test_case.agent_id,
        name=test_case.name,
        description=test_case.description,
        category=test_case.category,
        input=test_case.input,
        expected_output=test_case.expected_output,
        context=test_case.context,
        source=test_case.source,
        source_session_id=test_case.source_session_id,
        source_message_id=test_case.source_message_id,
        is_positive=test_case.is_positive,
        evaluation_type=test_case.evaluation_type,
        evaluation_criteria=test_case.evaluation_criteria,
        last_result=last_result,
        pass_count=test_case.pass_count or 0,
        fail_count=test_case.fail_count or 0,
        is_active=test_case.is_active,
        is_baseline=test_case.is_baseline,
        created_at=test_case.created_at,
        updated_at=test_case.updated_at,
        last_evaluated_at=test_case.last_evaluated_at,
    )


@router.get("/agents/{agent_id}/test-cases", response_model=TestCaseListResponse)
async def list_test_cases(
    agent_id: str,
    category: Optional[str] = None,
    is_baseline: Optional[bool] = None,
    is_active: Optional[bool] = None,
    source: Optional[str] = None,
    limit: int = Query(50, ge=1, le=250),
    offset: int = Query(0, ge=0),
    agent_info: AgentInfo = Depends(verify_agent_access),
    db: AsyncSession = Depends(get_db),
):
    """
    List test cases for an agent with filters.

    Note: Built-in agents (data-retrieval, analysis, etc.) return empty list
    as they use predefined behavior. Test cases are only for custom agents.
    """
    # Built-in agents don't support test cases - return empty list
    if agent_info.is_builtin:
        return TestCaseListResponse(items=[], total=0, offset=offset, limit=limit)

    # Parse UUID for custom agents
    agent_uuid = _get_agent_uuid(agent_id)

    # Build query
    query = select(AgentTestCase).where(AgentTestCase.agent_id == agent_uuid)
    count_query = select(func.count()).select_from(AgentTestCase).where(AgentTestCase.agent_id == agent_uuid)

    # Apply filters
    if category:
        query = query.where(AgentTestCase.category == category)
        count_query = count_query.where(AgentTestCase.category == category)

    if is_baseline is not None:
        query = query.where(AgentTestCase.is_baseline == is_baseline)
        count_query = count_query.where(AgentTestCase.is_baseline == is_baseline)

    if is_active is not None:
        query = query.where(AgentTestCase.is_active == is_active)
        count_query = count_query.where(AgentTestCase.is_active == is_active)

    if source:
        query = query.where(AgentTestCase.source == source)
        count_query = count_query.where(AgentTestCase.source == source)

    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply ordering and pagination
    query = query.order_by(desc(AgentTestCase.created_at))
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    test_cases = result.scalars().all()

    return TestCaseListResponse(
        items=[test_case_to_response(tc) for tc in test_cases],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post("/agents/{agent_id}/test-cases", response_model=TestCaseResponse, status_code=status.HTTP_201_CREATED)
async def create_test_case(
    agent_id: str,
    request: CreateTestCaseRequest,
    agent_info: AgentInfo = Depends(verify_agent_access),
    db: AsyncSession = Depends(get_db),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Manually create a test case for a custom agent.

    Note: Built-in agents don't support custom test cases.
    """
    # Built-in agents don't support test cases
    if agent_info.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Test cases are only supported for custom agents. Built-in agent '{agent_id}' uses predefined behavior."
        )

    agent_uuid = _get_agent_uuid(agent_id)

    # Generate name if not provided
    name = request.name or AgentTestCase.generate_name_from_input(request.input)

    test_case = AgentTestCase(
        tenant_id=tenant_id,
        agent_id=agent_uuid,
        name=name,
        description=request.description,
        category=request.category,
        input=request.input,
        expected_output=request.expected_output,
        context=request.context,
        source='manual',
        is_positive=request.is_positive,
        evaluation_type=request.evaluation_type,
        evaluation_criteria=request.evaluation_criteria,
        is_baseline=request.is_baseline,
    )

    db.add(test_case)
    await db.flush()
    await db.refresh(test_case)

    return test_case_to_response(test_case)


@router.get("/agents/{agent_id}/test-cases/{test_case_id}", response_model=TestCaseResponse)
async def get_test_case(
    agent_id: str,
    test_case_id: UUID,
    agent_info: AgentInfo = Depends(verify_agent_access),
    db: AsyncSession = Depends(get_db),
):
    """
    Get a specific test case.
    """
    if agent_info.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Test case not found"
        )

    agent_uuid = _get_agent_uuid(agent_id)

    result = await db.execute(
        select(AgentTestCase).where(
            AgentTestCase.id == test_case_id,
            AgentTestCase.agent_id == agent_uuid
        )
    )
    test_case = result.scalar_one_or_none()

    if not test_case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Test case not found"
        )

    return test_case_to_response(test_case)


@router.patch("/agents/{agent_id}/test-cases/{test_case_id}", response_model=TestCaseResponse)
async def update_test_case(
    agent_id: str,
    test_case_id: UUID,
    request: UpdateTestCaseRequest,
    agent_info: AgentInfo = Depends(verify_agent_access),
    db: AsyncSession = Depends(get_db),
):
    """
    Update a test case.
    """
    if agent_info.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Test case not found"
        )

    agent_uuid = _get_agent_uuid(agent_id)

    result = await db.execute(
        select(AgentTestCase).where(
            AgentTestCase.id == test_case_id,
            AgentTestCase.agent_id == agent_uuid
        )
    )
    test_case = result.scalar_one_or_none()

    if not test_case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Test case not found"
        )

    # Update fields via safe_update (S25: blocks tenant_id, id, etc.)
    from app.utils.safe_update import safe_update
    from app.schemas.agent_studio import UpdateTestCaseRequest
    update_data = request.model_dump(exclude_unset=True)
    safe_update(test_case, update_data, UpdateTestCaseRequest)

    test_case.updated_at = datetime.utcnow()

    await db.flush()
    await db.refresh(test_case)

    return test_case_to_response(test_case)


@router.delete("/agents/{agent_id}/test-cases/{test_case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_test_case(
    agent_id: str,
    test_case_id: UUID,
    agent_info: AgentInfo = Depends(verify_agent_access),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a test case.
    """
    if agent_info.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Test case not found"
        )

    agent_uuid = _get_agent_uuid(agent_id)

    result = await db.execute(
        select(AgentTestCase).where(
            AgentTestCase.id == test_case_id,
            AgentTestCase.agent_id == agent_uuid
        )
    )
    test_case = result.scalar_one_or_none()

    if not test_case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Test case not found"
        )

    await db.delete(test_case)
    await db.commit()


@router.post("/agents/{agent_id}/test-cases/import", response_model=ImportTestCasesResult)
async def import_test_cases(
    agent_id: str,
    file: UploadFile = File(...),
    agent_info: AgentInfo = Depends(verify_agent_access),
    db: AsyncSession = Depends(get_db),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Bulk import test cases from CSV or JSON file.

    CSV format: name,input,expected_output,category,is_positive
    JSON format: [{"name": "...", "input": "...", "expected_output": "...", ...}]

    Note: Only supported for custom agents.
    """
    if agent_info.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Test cases are only supported for custom agents. Built-in agent '{agent_id}' uses predefined behavior."
        )

    agent_uuid = _get_agent_uuid(agent_id)
    content = await file.read()
    content_str = content.decode('utf-8')

    imported = 0
    skipped = 0
    errors = []

    try:
        # Try JSON first
        if file.filename.endswith('.json') or content_str.strip().startswith('['):
            test_cases_data = json.loads(content_str)

            for i, item in enumerate(test_cases_data):
                try:
                    if not item.get('input'):
                        errors.append(f"Row {i+1}: Missing 'input' field")
                        skipped += 1
                        continue

                    test_case = AgentTestCase(
                        tenant_id=tenant_id,
                        agent_id=agent_uuid,
                        name=item.get('name') or AgentTestCase.generate_name_from_input(item['input']),
                        description=item.get('description'),
                        category=item.get('category'),
                        input=item['input'],
                        expected_output=item.get('expected_output'),
                        context=item.get('context'),
                        source='imported',
                        is_positive=item.get('is_positive', True),
                        evaluation_type=item.get('evaluation_type', 'llm_judge'),
                        evaluation_criteria=item.get('evaluation_criteria'),
                        is_baseline=item.get('is_baseline', False),
                    )
                    db.add(test_case)
                    imported += 1
                except Exception as e:
                    errors.append(f"Row {i+1}: {str(e)}")
                    skipped += 1

        # Try CSV
        else:
            reader = csv.DictReader(io.StringIO(content_str))

            for i, row in enumerate(reader):
                try:
                    if not row.get('input'):
                        errors.append(f"Row {i+1}: Missing 'input' field")
                        skipped += 1
                        continue

                    is_positive = row.get('is_positive', 'true').lower() in ('true', '1', 'yes')
                    is_baseline = row.get('is_baseline', 'false').lower() in ('true', '1', 'yes')

                    test_case = AgentTestCase(
                        tenant_id=tenant_id,
                        agent_id=agent_uuid,
                        name=row.get('name') or AgentTestCase.generate_name_from_input(row['input']),
                        description=row.get('description'),
                        category=row.get('category'),
                        input=row['input'],
                        expected_output=row.get('expected_output'),
                        source='imported',
                        is_positive=is_positive,
                        evaluation_type=row.get('evaluation_type', 'llm_judge'),
                        is_baseline=is_baseline,
                    )
                    db.add(test_case)
                    imported += 1
                except Exception as e:
                    errors.append(f"Row {i+1}: {str(e)}")
                    skipped += 1

        await db.commit()

    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON format: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse file: {str(e)}"
        )

    return ImportTestCasesResult(
        imported=imported,
        skipped=skipped,
        errors=errors[:10],  # Limit errors to first 10
    )


@router.post("/agents/{agent_id}/test-cases/generate", response_model=GenerateTestsResponse)
async def generate_test_cases(
    agent_id: str,
    request: GenerateTestsRequest,
    agent_info: AgentInfo = Depends(verify_agent_access),
    db: AsyncSession = Depends(get_db),
    tenant_id: UUID = Depends(get_current_tenant_id),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Auto-generate test cases from agent instructions using an LLM.

    Uses gpt-4o-mini to create diverse test scenarios based on the agent's
    instructions. Deduplicates against existing test case inputs.
    """
    if agent_info.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Test case generation is only supported for custom agents.",
        )

    agent_uuid = _get_agent_uuid(agent_id)

    # Fetch existing test inputs for deduplication
    existing_inputs: set[str] = set()
    result = await db.execute(
        select(AgentTestCase.input).where(AgentTestCase.agent_id == agent_uuid)
    )
    for row in result.scalars().all():
        existing_inputs.add(row.strip().lower())

    # Build LLM prompt
    system_prompt = """\
You are a QA engineer generating test cases for an AI agent. Given the agent's \
instructions, produce diverse test scenarios that cover normal usage, edge cases, \
and potential failure modes.

Return valid JSON (no markdown fences):
{
  "test_cases": [
    {
      "input": "user message to test",
      "expected_output": "what a good response should contain",
      "category": "category name",
      "evaluation_type": "llm_judge"
    }
  ]
}

Rules:
1. Generate exactly the requested number of test cases.
2. Cover different categories: happy path, edge cases, boundary conditions, adversarial.
3. expected_output describes WHAT the response should contain, not the exact text.
4. evaluation_type should be "llm_judge" for open-ended, "contains" for keyword checks.
5. Make inputs realistic — they should read like real user messages.
6. Avoid duplicating these existing test inputs: {existing_inputs}
"""

    user_message = f"Generate {request.count} test cases for this agent:\n\n{request.instructions}"

    existing_str = "; ".join(list(existing_inputs)[:20]) if existing_inputs else "(none)"
    final_system = system_prompt.replace("{existing_inputs}", existing_str)

    # Call LLM — resolve tenant provider dynamically
    from app.services.llm_gateway import resolve_effective_provider
    from app.config import get_settings

    provider = await resolve_effective_provider(db, tenant_id)
    if not provider:
        # Bootstrap fallback — no tenant provider configured yet
        settings = get_settings()
        api_key = settings.OPENROUTER_API_KEY
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No LLM provider configured. Please add one in Settings > AI Models.",
            )
        model = settings.DEFAULT_LLM_MODEL or "openai/gpt-4o-mini"
        provider_type = "openrouter"
        api_base = settings.OPENROUTER_BASE_URL
    else:
        api_key = provider.api_key
        model = provider.model
        provider_type = provider.provider_type
        api_base = provider.api_base

    try:
        if provider_type == "anthropic":
            import anthropic

            # Strip 'anthropic/' prefix if present (e.g. 'anthropic/claude-3-haiku' → 'claude-3-haiku')
            anthropic_model = model.removeprefix("anthropic/")
            anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)
            anthropic_response = await anthropic_client.messages.create(
                model=anthropic_model,
                system=final_system,
                messages=[{"role": "user", "content": user_message}],
                temperature=0.7,
                max_tokens=3000,
            )
            raw = anthropic_response.content[0].text or "{}"
        else:
            from openai import AsyncOpenAI

            if provider_type == "openrouter":
                client = AsyncOpenAI(
                    base_url=api_base or "https://openrouter.ai/api/v1",
                    api_key=api_key,
                )
            else:
                # openai (or any other OpenAI-compatible provider)
                client = AsyncOpenAI(api_key=api_key)

            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": final_system},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.7,
                max_tokens=3000,
            )
            raw = response.choices[0].message.content or "{}"

        parsed = json.loads(raw)
        cases = [GeneratedTestCase(**tc) for tc in parsed.get("test_cases", [])]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to generate test cases: {e}",
        )

    # Deduplicate against existing
    filtered = [
        tc for tc in cases
        if tc.input.strip().lower() not in existing_inputs
    ]

    return GenerateTestsResponse(test_cases=filtered)


@router.get("/agents/{agent_id}/test-cases/export")
async def export_test_cases(
    agent_id: str,
    format: str = Query("json", pattern="^(json|csv)$"),
    agent_info: AgentInfo = Depends(verify_agent_access),
    db: AsyncSession = Depends(get_db),
):
    """
    Export test cases to JSON or CSV file.

    Note: Only supported for custom agents.
    """
    if agent_info.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Export is only supported for custom agents. Built-in agent '{agent_id}' uses predefined behavior."
        )

    agent_uuid = _get_agent_uuid(agent_id)

    # Get all active test cases
    result = await db.execute(
        select(AgentTestCase).where(
            AgentTestCase.agent_id == agent_uuid,
            AgentTestCase.is_active.is_(True)
        ).order_by(AgentTestCase.created_at)
    )
    test_cases = result.scalars().all()

    if format == "json":
        data = [
            {
                "name": tc.name,
                "description": tc.description,
                "category": tc.category,
                "input": tc.input,
                "expected_output": tc.expected_output,
                "context": tc.context,
                "is_positive": tc.is_positive,
                "evaluation_type": tc.evaluation_type,
                "evaluation_criteria": tc.evaluation_criteria,
                "is_baseline": tc.is_baseline,
            }
            for tc in test_cases
        ]
        content = json.dumps(data, indent=2)
        media_type = "application/json"
        filename = f"{agent_info.name}_test_cases.json"

    else:  # csv
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=[
            'name', 'category', 'input', 'expected_output', 'is_positive', 'evaluation_type', 'is_baseline'
        ])
        writer.writeheader()
        for tc in test_cases:
            writer.writerow({
                'name': tc.name,
                'category': tc.category,
                'input': tc.input,
                'expected_output': tc.expected_output,
                'is_positive': tc.is_positive,
                'evaluation_type': tc.evaluation_type,
                'is_baseline': tc.is_baseline,
            })
        content = output.getvalue()
        media_type = "text/csv"
        filename = f"{agent_info.name}_test_cases.csv"

    return StreamingResponse(
        io.BytesIO(content.encode('utf-8')),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
