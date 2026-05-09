"""
Workflow DSL API endpoints
Export and import workflows as YAML/JSON DSL
"""

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.core.database import get_db
from app.models.workflow import Workflow, WorkflowVersion
from app.auth.dependencies import get_current_tenant_id, get_current_user_id
from app.services.workflow_dsl import (
    workflow_dsl_service,
    DSLValidationResult
)

router = APIRouter()


# --- Request/Response Models ---

class ExportRequest(BaseModel):
    """Request to export workflow"""
    format: str = "yaml"  # yaml or json
    version_id: Optional[uuid.UUID] = None  # Export specific version
    include_metadata: bool = True


class ImportRequest(BaseModel):
    """Request to import workflow"""
    content: str
    format: str = "yaml"  # yaml or json
    name_override: Optional[str] = None
    create_version: bool = True


class ValidateRequest(BaseModel):
    """Request to validate DSL"""
    content: str
    format: str = "yaml"


class ExportResponse(BaseModel):
    """Export response"""
    content: str
    format: str
    workflow_id: str
    workflow_name: str
    version: Optional[int] = None
    validation: DSLValidationResult


class ImportResponse(BaseModel):
    """Import response"""
    success: bool
    workflow_id: Optional[str] = None
    name: Optional[str] = None
    version: Optional[int] = None
    validation: DSLValidationResult
    errors: list[str] = []


# --- API Endpoints ---

@router.get("/workflows/{workflow_id}/export")
async def export_workflow(
    workflow_id: uuid.UUID,
    format: str = Query("yaml", description="Export format: yaml or json"),
    version_id: Optional[uuid.UUID] = Query(None, description="Export specific version"),
    download: bool = Query(False, description="Return as downloadable file"),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """
    Export workflow as YAML or JSON DSL

    - Returns human-readable format suitable for version control
    - Can export specific version or current workflow state
    - Includes variable definitions and metadata
    """
    # Get workflow
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

    # Get spec (from version or current workflow)
    spec = workflow.spec
    version_number = None
    version_tag = None

    if version_id:
        version_stmt = select(WorkflowVersion).where(
            and_(
                WorkflowVersion.id == version_id,
                WorkflowVersion.workflow_id == workflow_id,
                WorkflowVersion.tenant_id == tenant_id
            )
        )
        version_result = await db.execute(version_stmt)
        version = version_result.scalar_one_or_none()

        if not version:
            raise HTTPException(status_code=404, detail="Version not found")

        spec = version.spec
        version_number = version.version_number
        version_tag = version.version_tag

    # Export
    result = workflow_dsl_service.export_workflow(
        workflow_spec=spec,
        name=workflow.name,
        description=workflow.description,
        version=version_number or 1,
        version_tag=version_tag,
    )

    content = result.yaml_content if format.lower() == "yaml" else result.json_content
    content_type = "text/yaml" if format.lower() == "yaml" else "application/json"
    file_ext = "yaml" if format.lower() == "yaml" else "json"

    if download:
        filename = f"{workflow.name.replace(' ', '_')}_{version_number or 'current'}.{file_ext}"
        return Response(
            content=content,
            media_type=content_type,
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )

    return ExportResponse(
        content=content,
        format=format.lower(),
        workflow_id=str(workflow_id),
        workflow_name=workflow.name,
        version=version_number,
        validation=result.validation
    )


@router.post("/workflows/{workflow_id}/import", response_model=ImportResponse)
async def import_to_workflow(
    workflow_id: uuid.UUID,
    request: ImportRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """
    Import DSL content to update existing workflow

    - Updates workflow spec from DSL
    - Optionally creates a new version
    """
    # Get workflow
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

    # Import DSL
    result = workflow_dsl_service.import_workflow(
        dsl_content=request.content,
        format=request.format
    )

    if not result.success:
        return ImportResponse(
            success=False,
            validation=result.validation,
            errors=result.errors
        )

    # Update workflow
    workflow.spec = result.spec
    if request.name_override:
        workflow.name = request.name_override
    elif result.metadata and result.metadata.get("name"):
        # Only update name if explicitly different
        pass  # Keep existing name

    # Create version if requested
    version_number = None
    if request.create_version:
        from app.api.v1.workflow_versions import compute_spec_hash, count_nodes_and_edges
        from sqlalchemy import func

        # Get next version number
        max_version_stmt = select(func.max(WorkflowVersion.version_number)).where(
            WorkflowVersion.workflow_id == workflow_id
        )
        max_result = await db.execute(max_version_stmt)
        max_version = max_result.scalar() or 0
        version_number = max_version + 1

        spec_hash = compute_spec_hash(result.spec)
        node_count, edge_count = count_nodes_and_edges(result.spec)

        new_version = WorkflowVersion(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            version_number=version_number,
            version_tag=result.metadata.get("version_tag") if result.metadata else None,
            spec=result.spec,
            spec_hash=spec_hash,
            description=f"Imported from {request.format.upper()} DSL",
            created_by=user_id,
            is_draft=True,
            is_active=False,
            node_count=node_count,
            edge_count=edge_count,
        )
        db.add(new_version)

    await db.commit()

    return ImportResponse(
        success=True,
        workflow_id=str(workflow_id),
        name=workflow.name,
        version=version_number,
        validation=result.validation
    )


@router.post("/workflows/import", response_model=ImportResponse)
async def create_workflow_from_dsl(
    request: ImportRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """
    Create new workflow from DSL content

    - Creates a new workflow from YAML/JSON DSL
    - Extracts metadata (name, description) from DSL
    - Optionally creates initial version
    """
    # Import DSL
    result = workflow_dsl_service.import_workflow(
        dsl_content=request.content,
        format=request.format
    )

    if not result.success:
        return ImportResponse(
            success=False,
            validation=result.validation,
            errors=result.errors
        )

    # Determine workflow name
    name = request.name_override
    if not name and result.metadata:
        name = result.metadata.get("name")
    if not name:
        name = "Imported Workflow"

    # Create workflow
    new_workflow = Workflow(
        tenant_id=tenant_id,
        user_id=user_id,
        name=name,
        description=result.metadata.get("description") if result.metadata else None,
        spec=result.spec,
    )
    db.add(new_workflow)
    await db.flush()  # Get the ID

    # Create initial version if requested
    version_number = None
    if request.create_version:
        from app.api.v1.workflow_versions import compute_spec_hash, count_nodes_and_edges

        spec_hash = compute_spec_hash(result.spec)
        node_count, edge_count = count_nodes_and_edges(result.spec)

        new_version = WorkflowVersion(
            workflow_id=new_workflow.id,
            tenant_id=tenant_id,
            version_number=1,
            version_tag=result.metadata.get("version_tag") if result.metadata else None,
            spec=result.spec,
            spec_hash=spec_hash,
            description="Initial version from DSL import",
            created_by=user_id,
            is_draft=True,
            is_active=False,
            node_count=node_count,
            edge_count=edge_count,
        )
        db.add(new_version)
        version_number = 1

    await db.commit()

    return ImportResponse(
        success=True,
        workflow_id=str(new_workflow.id),
        name=name,
        version=version_number,
        validation=result.validation
    )


@router.post("/workflows/validate-dsl", response_model=DSLValidationResult)
async def validate_dsl(
    request: ValidateRequest,
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """
    Validate DSL content without importing

    - Checks syntax and structure
    - Validates node references
    - Reports warnings for potential issues
    """
    return workflow_dsl_service.validate_dsl(
        dsl_content=request.content,
        format=request.format
    )


@router.get("/workflows/dsl-schema")
async def get_dsl_schema():
    """
    Get the JSON Schema for workflow DSL

    - Useful for IDE autocompletion
    - Documents all supported node types
    """
    from app.services.workflow_dsl import WorkflowDSL

    return {
        "schema": WorkflowDSL.model_json_schema(),
        "supported_node_types": list(workflow_dsl_service.NODE_TYPE_NAMES.keys()),
        "node_type_names": workflow_dsl_service.NODE_TYPE_NAMES,
        "variable_pattern": "{{node_id.output}} or {{variable_name}}",
        "supported_formats": ["yaml", "json"],
    }
