"""
Conversation Variables API endpoints
Manage persistent variables across workflow conversation turns
"""

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.core.database import get_db
from app.models.workflow import Workflow
from app.auth.dependencies import get_current_tenant_id
from app.services.conversation_variables import (
    conversation_variables_service,
    VariableType,
    initialize_conversation_for_workflow,
)

router = APIRouter()


# --- Request/Response Models ---

class SetVariableRequest(BaseModel):
    """Request to set a variable"""
    name: str
    value: Any
    type: Optional[VariableType] = None
    source_node: Optional[str] = None


class UpdateVariablesRequest(BaseModel):
    """Request to update multiple variables"""
    variables: Dict[str, Any]
    source_node: Optional[str] = None


class AppendArrayRequest(BaseModel):
    """Request to append to array variable"""
    name: str
    value: Any
    max_length: Optional[int] = None
    source_node: Optional[str] = None


class UpdateObjectRequest(BaseModel):
    """Request to update object variable"""
    name: str
    updates: Dict[str, Any]
    source_node: Optional[str] = None


class IncrementRequest(BaseModel):
    """Request to increment numeric variable"""
    name: str
    delta: float = 1
    source_node: Optional[str] = None


class VariableResponse(BaseModel):
    """Response with variable value"""
    name: str
    value: Any
    type: str
    turn_number: int
    updated_at: str


class ConversationStateResponse(BaseModel):
    """Response with full conversation state"""
    session_id: str
    workflow_id: str
    turn_number: int
    variables: Dict[str, Any]
    created_at: str
    updated_at: str


class VariableHistoryResponse(BaseModel):
    """Response with variable history"""
    name: str
    current: Optional[VariableResponse] = None
    history: List[VariableResponse] = Field(default_factory=list)


# --- API Endpoints ---

@router.post("/workflows/{workflow_id}/conversations/{session_id}/initialize")
async def initialize_conversation(
    workflow_id: uuid.UUID,
    session_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """
    Initialize a conversation session with workflow variables

    - Creates a new conversation state
    - Initializes variables from workflow spec
    - Returns initial state
    """
    # Verify workflow exists
    workflow_stmt = select(Workflow).where(
        and_(
            Workflow.id == workflow_id,
            Workflow.tenant_id == tenant_id
        )
    )
    workflow_result = await db.execute(workflow_stmt)
    workflow = workflow_result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Initialize conversation
    state = initialize_conversation_for_workflow(
        session_id=session_id,
        workflow_id=str(workflow_id),
        workflow_spec=workflow.spec
    )

    return ConversationStateResponse(
        session_id=state.session_id,
        workflow_id=state.workflow_id,
        turn_number=state.turn_number,
        variables=conversation_variables_service.get_all_variables(
            session_id, str(workflow_id)
        ),
        created_at=state.created_at,
        updated_at=state.updated_at,
    )


@router.get("/workflows/{workflow_id}/conversations/{session_id}")
async def get_conversation_state(
    workflow_id: uuid.UUID,
    session_id: str,
    include_history: bool = Query(False, description="Include variable history"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """
    Get current conversation state

    - Returns all current variable values
    - Optionally includes history
    """
    state = conversation_variables_service.get_session(session_id, str(workflow_id))

    if not state:
        raise HTTPException(status_code=404, detail="Conversation not found")

    response = {
        "session_id": state.session_id,
        "workflow_id": state.workflow_id,
        "turn_number": state.turn_number,
        "variables": conversation_variables_service.get_all_variables(
            session_id, str(workflow_id)
        ),
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }

    if include_history:
        response["variable_details"] = {
            name: {
                "current": var.current.model_dump() if var.current else None,
                "history": [v.model_dump() for v in var.history]
            }
            for name, var in state.variables.items()
        }

    return response


@router.post("/workflows/{workflow_id}/conversations/{session_id}/turn")
async def start_turn(
    workflow_id: uuid.UUID,
    session_id: str,
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """
    Start a new conversation turn

    - Increments turn counter
    - Returns new turn number
    """
    turn_number = conversation_variables_service.start_turn(
        session_id, str(workflow_id)
    )

    return {
        "session_id": session_id,
        "workflow_id": str(workflow_id),
        "turn_number": turn_number,
    }


@router.get("/workflows/{workflow_id}/conversations/{session_id}/variables")
async def get_all_variables(
    workflow_id: uuid.UUID,
    session_id: str,
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Get all current variable values"""
    variables = conversation_variables_service.get_all_variables(
        session_id, str(workflow_id)
    )
    return {"variables": variables}


@router.get("/workflows/{workflow_id}/conversations/{session_id}/variables/{name}")
async def get_variable(
    workflow_id: uuid.UUID,
    session_id: str,
    name: str,
    include_history: bool = Query(False),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Get a specific variable's value and optionally history"""
    if include_history:
        history = conversation_variables_service.get_variable_history(
            session_id, str(workflow_id), name
        )
        if not history:
            raise HTTPException(status_code=404, detail="Variable not found")

        return VariableHistoryResponse(
            name=name,
            current=VariableResponse(
                name=name,
                value=history.current.value,
                type=history.current.type,
                turn_number=history.current.turn_number,
                updated_at=history.current.updated_at,
            ) if history.current else None,
            history=[
                VariableResponse(
                    name=name,
                    value=v.value,
                    type=v.type,
                    turn_number=v.turn_number,
                    updated_at=v.updated_at,
                )
                for v in history.history
            ]
        )
    else:
        value = conversation_variables_service.get_variable(
            session_id, str(workflow_id), name
        )
        return {"name": name, "value": value}


@router.post("/workflows/{workflow_id}/conversations/{session_id}/variables")
async def set_variable(
    workflow_id: uuid.UUID,
    session_id: str,
    request: SetVariableRequest,
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Set a variable value"""
    result = conversation_variables_service.set_variable(
        session_id=session_id,
        workflow_id=str(workflow_id),
        name=request.name,
        value=request.value,
        source_node=request.source_node,
        var_type=request.type,
    )

    return VariableResponse(
        name=request.name,
        value=result.value,
        type=result.type,
        turn_number=result.turn_number,
        updated_at=result.updated_at,
    )


@router.put("/workflows/{workflow_id}/conversations/{session_id}/variables")
async def update_variables(
    workflow_id: uuid.UUID,
    session_id: str,
    request: UpdateVariablesRequest,
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Update multiple variables at once"""
    results = {}
    for name, value in request.variables.items():
        result = conversation_variables_service.set_variable(
            session_id=session_id,
            workflow_id=str(workflow_id),
            name=name,
            value=value,
            source_node=request.source_node,
        )
        results[name] = result.value

    return {"updated": results}


@router.post("/workflows/{workflow_id}/conversations/{session_id}/variables/append")
async def append_to_array(
    workflow_id: uuid.UUID,
    session_id: str,
    request: AppendArrayRequest,
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Append value to an array variable"""
    result = conversation_variables_service.append_to_array(
        session_id=session_id,
        workflow_id=str(workflow_id),
        name=request.name,
        value=request.value,
        source_node=request.source_node,
        max_length=request.max_length,
    )

    return {"name": request.name, "value": result, "length": len(result)}


@router.post("/workflows/{workflow_id}/conversations/{session_id}/variables/update-object")
async def update_object(
    workflow_id: uuid.UUID,
    session_id: str,
    request: UpdateObjectRequest,
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Update fields in an object variable"""
    result = conversation_variables_service.update_object(
        session_id=session_id,
        workflow_id=str(workflow_id),
        name=request.name,
        updates=request.updates,
        source_node=request.source_node,
    )

    return {"name": request.name, "value": result}


@router.post("/workflows/{workflow_id}/conversations/{session_id}/variables/increment")
async def increment_variable(
    workflow_id: uuid.UUID,
    session_id: str,
    request: IncrementRequest,
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Increment a numeric variable"""
    result = conversation_variables_service.increment(
        session_id=session_id,
        workflow_id=str(workflow_id),
        name=request.name,
        delta=request.delta,
        source_node=request.source_node,
    )

    return {"name": request.name, "value": result}


@router.delete("/workflows/{workflow_id}/conversations/{session_id}/variables/{name}")
async def reset_variable(
    workflow_id: uuid.UUID,
    session_id: str,
    name: str,
    default: Optional[Any] = Query(None, description="Default value to reset to"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Reset a variable to its default value"""
    conversation_variables_service.reset_variable(
        session_id, str(workflow_id), name, default
    )
    return {"success": True, "name": name, "reset_to": default}


@router.delete("/workflows/{workflow_id}/conversations/{session_id}")
async def clear_conversation(
    workflow_id: uuid.UUID,
    session_id: str,
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Clear all variables and delete conversation state"""
    success = conversation_variables_service.clear_session(
        session_id, str(workflow_id)
    )
    return {"success": success}


@router.get("/workflows/{workflow_id}/conversations/{session_id}/context")
async def get_conversation_context(
    workflow_id: uuid.UUID,
    session_id: str,
    include_history: bool = Query(False),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """
    Get conversation context for template substitution

    Returns data suitable for {{conversation.X}} variable resolution
    """
    context = conversation_variables_service.get_conversation_context(
        session_id, str(workflow_id), include_history
    )
    return context


@router.get("/workflows/{workflow_id}/conversations/{session_id}/export")
async def export_conversation(
    workflow_id: uuid.UUID,
    session_id: str,
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Export conversation state for backup or debugging"""
    data = conversation_variables_service.export_session(
        session_id, str(workflow_id)
    )
    if not data:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return data


@router.post("/workflows/{workflow_id}/conversations/import")
async def import_conversation(
    workflow_id: uuid.UUID,
    session_data: Dict[str, Any],
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Import conversation state from exported data"""
    # Validate workflow_id matches
    if session_data.get("workflow_id") != str(workflow_id):
        raise HTTPException(
            status_code=400,
            detail="Workflow ID in session data doesn't match URL"
        )

    state = conversation_variables_service.import_session(session_data)
    return ConversationStateResponse(
        session_id=state.session_id,
        workflow_id=state.workflow_id,
        turn_number=state.turn_number,
        variables=conversation_variables_service.get_all_variables(
            state.session_id, state.workflow_id
        ),
        created_at=state.created_at,
        updated_at=state.updated_at,
    )
