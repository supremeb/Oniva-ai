"""
Workflow DSL Service
Vellum-inspired workflow export/import as YAML DSL

Supports:
- Exporting workflows to human-readable YAML format
- Importing workflows from YAML DSL
- Validation and schema enforcement
- Variable resolution and reference tracking
- Version-aware export/import

YAML DSL Format:
```yaml
workflow:
  name: "My Workflow"
  description: "Description here"
  version: 1
  variables:
    - name: "input_text"
      type: "string"
      required: true
      default: ""

  nodes:
    - id: "start_1"
      type: "start"
      position: {x: 100, y: 100}
      data:
        title: "Start"
        variables:
          - name: "query"
            type: "string"

    - id: "llm_1"
      type: "llm"
      position: {x: 300, y: 100}
      data:
        title: "Generate Response"
        model: "gpt-4"
        prompt: "{{start_1.query}}"
        temperature: 0.7

  edges:
    - source: "start_1"
      target: "llm_1"
      type: "default"
```
"""

import json
import logging
import re
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# --- Pydantic Models for DSL ---

class VariableDefinition(BaseModel):
    """Variable definition in workflow DSL"""
    name: str
    type: str = "string"  # string, number, boolean, array, object
    required: bool = False
    default: Optional[Any] = None
    description: Optional[str] = None


class NodePosition(BaseModel):
    """Node position in canvas"""
    x: float
    y: float


class NodeDefinition(BaseModel):
    """Node definition in workflow DSL"""
    id: str
    type: str
    position: NodePosition
    data: Dict[str, Any] = Field(default_factory=dict)


class EdgeDefinition(BaseModel):
    """Edge definition in workflow DSL"""
    id: Optional[str] = None
    source: str
    target: str
    source_handle: Optional[str] = None
    target_handle: Optional[str] = None
    type: str = "default"
    label: Optional[str] = None


class WorkflowMetadata(BaseModel):
    """Workflow metadata in DSL"""
    name: str
    description: Optional[str] = None
    version: int = 1
    version_tag: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    author: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class WorkflowDSL(BaseModel):
    """Complete workflow DSL structure"""
    workflow: WorkflowMetadata
    variables: List[VariableDefinition] = Field(default_factory=list)
    nodes: List[NodeDefinition]
    edges: List[EdgeDefinition] = Field(default_factory=list)


class DSLValidationResult(BaseModel):
    """Result of DSL validation"""
    valid: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0
    variable_count: int = 0
    referenced_variables: List[str] = Field(default_factory=list)
    undefined_references: List[str] = Field(default_factory=list)


class DSLExportResult(BaseModel):
    """Result of workflow export"""
    yaml_content: str
    json_content: str
    validation: DSLValidationResult


class DSLImportResult(BaseModel):
    """Result of workflow import"""
    success: bool
    spec: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    validation: DSLValidationResult
    errors: List[str] = Field(default_factory=list)


class WorkflowDSLService:
    """
    Service for exporting/importing workflows as YAML DSL

    Features:
    - Human-readable YAML format
    - Variable extraction and validation
    - Reference tracking ({{node_id.output}})
    - Schema validation
    - Round-trip fidelity
    """

    # Node type display names
    NODE_TYPE_NAMES = {
        "start": "Start",
        "output": "Output",
        "llm": "LLM",
        "http": "HTTP Request",
        "code": "Code",
        "condition": "Condition",
        "iteration": "Iteration",
        "classifier": "Classifier",
        "template": "Template",
        "aggregator": "Aggregator",
        "knowledge": "Knowledge",
        "skill": "Skill",
        "connector": "Connector",
        "agent": "Agent",
        "compliance": "Compliance",
        "wait": "Wait",
        "variable_assigner": "Variable Assigner",
        "list_operator": "List Operator",
        "document_extractor": "Document Extractor",
        "parameter_extractor": "Parameter Extractor",
        "ocr": "OCR",
        "notification": "Notification",
        "webhook_trigger": "Webhook Trigger",
        "subworkflow": "Sub-Workflow",
        "human_in_loop": "Human in Loop",
        "spreadsheet": "Spreadsheet",
        "guardrail": "Guardrail",
        "merge": "Merge",
        "error": "Error",
    }

    # Variable pattern for extraction: {{node_id.output}} or {{variable_name}}
    VARIABLE_PATTERN = re.compile(r'\{\{([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?)\}\}')

    def export_workflow(
        self,
        workflow_spec: Dict[str, Any],
        name: str,
        description: Optional[str] = None,
        version: int = 1,
        version_tag: Optional[str] = None,
        author: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> DSLExportResult:
        """
        Export workflow spec to YAML DSL format

        Args:
            workflow_spec: React Flow workflow specification
            name: Workflow name
            description: Workflow description
            version: Version number
            version_tag: Optional version tag (e.g., "v1.0.0")
            author: Author name/email
            tags: Optional list of tags

        Returns:
            DSLExportResult with YAML and JSON content
        """
        try:
            # Extract nodes and edges from spec
            nodes_data = workflow_spec.get("nodes", [])
            edges_data = workflow_spec.get("edges", [])

            # Build DSL structure
            nodes = []
            for node in nodes_data:
                node_def = NodeDefinition(
                    id=node.get("id", str(uuid.uuid4())),
                    type=node.get("type", "unknown"),
                    position=NodePosition(
                        x=node.get("position", {}).get("x", 0),
                        y=node.get("position", {}).get("y", 0)
                    ),
                    data=node.get("data", {})
                )
                nodes.append(node_def)

            edges = []
            for edge in edges_data:
                edge_def = EdgeDefinition(
                    id=edge.get("id"),
                    source=edge.get("source", ""),
                    target=edge.get("target", ""),
                    source_handle=edge.get("sourceHandle"),
                    target_handle=edge.get("targetHandle"),
                    type=edge.get("type", "default"),
                    label=edge.get("label")
                )
                edges.append(edge_def)

            # Extract variables from workflow spec or nodes
            variables = self._extract_variables(workflow_spec, nodes)

            # Build metadata
            metadata = WorkflowMetadata(
                name=name,
                description=description,
                version=version,
                version_tag=version_tag,
                created_at=datetime.utcnow().isoformat(),
                author=author,
                tags=tags or []
            )

            # Create DSL object
            dsl = WorkflowDSL(
                workflow=metadata,
                variables=variables,
                nodes=nodes,
                edges=edges
            )

            # Validate
            validation = self._validate_dsl(dsl)

            # Convert to YAML
            dsl_dict = self._dsl_to_dict(dsl)
            yaml_content = yaml.dump(
                dsl_dict,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
                width=120
            )

            # Also provide JSON version
            json_content = json.dumps(dsl_dict, indent=2, default=str)

            return DSLExportResult(
                yaml_content=yaml_content,
                json_content=json_content,
                validation=validation
            )

        except Exception as e:
            logger.exception(f"Failed to export workflow: {e}")
            return DSLExportResult(
                yaml_content="",
                json_content="",
                validation=DSLValidationResult(
                    valid=False,
                    errors=[f"Export failed: {str(e)}"]
                )
            )

    def import_workflow(
        self,
        dsl_content: str,
        format: str = "yaml"
    ) -> DSLImportResult:
        """
        Import workflow from YAML or JSON DSL

        Args:
            dsl_content: YAML or JSON string
            format: "yaml" or "json"

        Returns:
            DSLImportResult with workflow spec
        """
        try:
            # Parse content
            if format.lower() == "yaml":
                dsl_dict = yaml.safe_load(dsl_content)
            else:
                dsl_dict = json.loads(dsl_content)

            if not dsl_dict:
                return DSLImportResult(
                    success=False,
                    validation=DSLValidationResult(valid=False, errors=["Empty DSL content"]),
                    errors=["Empty DSL content"]
                )

            # Parse into DSL model
            dsl = self._dict_to_dsl(dsl_dict)

            # Validate
            validation = self._validate_dsl(dsl)

            if not validation.valid:
                return DSLImportResult(
                    success=False,
                    validation=validation,
                    errors=validation.errors
                )

            # Convert to workflow spec
            spec = self._dsl_to_spec(dsl)

            # Extract metadata
            metadata = {
                "name": dsl.workflow.name,
                "description": dsl.workflow.description,
                "version": dsl.workflow.version,
                "version_tag": dsl.workflow.version_tag,
                "author": dsl.workflow.author,
                "tags": dsl.workflow.tags,
            }

            return DSLImportResult(
                success=True,
                spec=spec,
                metadata=metadata,
                validation=validation
            )

        except yaml.YAMLError as e:
            return DSLImportResult(
                success=False,
                validation=DSLValidationResult(valid=False, errors=[f"Invalid YAML: {str(e)}"]),
                errors=[f"Invalid YAML: {str(e)}"]
            )
        except json.JSONDecodeError as e:
            return DSLImportResult(
                success=False,
                validation=DSLValidationResult(valid=False, errors=[f"Invalid JSON: {str(e)}"]),
                errors=[f"Invalid JSON: {str(e)}"]
            )
        except Exception as e:
            logger.exception(f"Failed to import workflow: {e}")
            return DSLImportResult(
                success=False,
                validation=DSLValidationResult(valid=False, errors=[f"Import failed: {str(e)}"]),
                errors=[f"Import failed: {str(e)}"]
            )

    def validate_dsl(self, dsl_content: str, format: str = "yaml") -> DSLValidationResult:
        """
        Validate DSL content without importing

        Args:
            dsl_content: YAML or JSON string
            format: "yaml" or "json"

        Returns:
            DSLValidationResult
        """
        try:
            if format.lower() == "yaml":
                dsl_dict = yaml.safe_load(dsl_content)
            else:
                dsl_dict = json.loads(dsl_content)

            if not dsl_dict:
                return DSLValidationResult(valid=False, errors=["Empty DSL content"])

            dsl = self._dict_to_dsl(dsl_dict)
            return self._validate_dsl(dsl)

        except Exception as e:
            return DSLValidationResult(valid=False, errors=[f"Validation failed: {str(e)}"])

    def _extract_variables(
        self,
        workflow_spec: Dict[str, Any],
        nodes: List[NodeDefinition]
    ) -> List[VariableDefinition]:
        """Extract variable definitions from workflow spec"""
        variables = []

        # Check for explicit variables in spec
        spec_variables = workflow_spec.get("variables", [])
        for var in spec_variables:
            if isinstance(var, dict):
                variables.append(VariableDefinition(
                    name=var.get("name", ""),
                    type=var.get("type", "string"),
                    required=var.get("required", False),
                    default=var.get("default"),
                    description=var.get("description")
                ))

        # Extract from start node if present
        for node in nodes:
            if node.type == "start":
                start_vars = node.data.get("variables", [])
                for var in start_vars:
                    if isinstance(var, dict):
                        var_name = var.get("name", var.get("variable", ""))
                        if var_name and not any(v.name == var_name for v in variables):
                            variables.append(VariableDefinition(
                                name=var_name,
                                type=var.get("type", "string"),
                                required=var.get("required", False),
                                default=var.get("default"),
                                description=var.get("description")
                            ))

        return variables

    def _validate_dsl(self, dsl: WorkflowDSL) -> DSLValidationResult:
        """Validate DSL structure and references"""
        errors = []
        warnings = []

        # Collect all node IDs
        node_ids = {node.id for node in dsl.nodes}

        # Check for required nodes
        has_start = any(node.type == "start" for node in dsl.nodes)
        has_output = any(node.type == "output" for node in dsl.nodes)

        if not has_start:
            warnings.append("Workflow has no start node")
        if not has_output:
            warnings.append("Workflow has no output node")

        # Validate edges reference existing nodes
        for edge in dsl.edges:
            if edge.source not in node_ids:
                errors.append(f"Edge references non-existent source node: {edge.source}")
            if edge.target not in node_ids:
                errors.append(f"Edge references non-existent target node: {edge.target}")

        # Extract all variable references
        referenced_vars = set()
        for node in dsl.nodes:
            refs = self._extract_references(node.data)
            referenced_vars.update(refs)

        # Check for undefined references
        defined_vars = {var.name for var in dsl.variables}
        defined_vars.update(node_ids)  # Node IDs can be referenced

        undefined_refs = []
        for ref in referenced_vars:
            # Extract base reference (node_id from node_id.output)
            base_ref = ref.split(".")[0]
            if base_ref not in defined_vars:
                undefined_refs.append(ref)

        if undefined_refs:
            warnings.append(f"Undefined variable references: {', '.join(undefined_refs)}")

        # Validate node types
        for node in dsl.nodes:
            if node.type not in self.NODE_TYPE_NAMES and node.type != "unknown":
                warnings.append(f"Unknown node type: {node.type}")

        # Check for duplicate node IDs
        seen_ids = set()
        for node in dsl.nodes:
            if node.id in seen_ids:
                errors.append(f"Duplicate node ID: {node.id}")
            seen_ids.add(node.id)

        return DSLValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            node_count=len(dsl.nodes),
            edge_count=len(dsl.edges),
            variable_count=len(dsl.variables),
            referenced_variables=list(referenced_vars),
            undefined_references=undefined_refs
        )

    def _extract_references(self, data: Any, refs: Optional[set] = None) -> set:
        """Extract all {{variable}} references from node data"""
        if refs is None:
            refs = set()

        if isinstance(data, str):
            matches = self.VARIABLE_PATTERN.findall(data)
            refs.update(matches)
        elif isinstance(data, dict):
            for value in data.values():
                self._extract_references(value, refs)
        elif isinstance(data, list):
            for item in data:
                self._extract_references(item, refs)

        return refs

    def _dsl_to_dict(self, dsl: WorkflowDSL) -> Dict[str, Any]:
        """Convert DSL model to dictionary for YAML export"""
        return {
            "workflow": {
                "name": dsl.workflow.name,
                "description": dsl.workflow.description,
                "version": dsl.workflow.version,
                "version_tag": dsl.workflow.version_tag,
                "author": dsl.workflow.author,
                "tags": dsl.workflow.tags,
                "created_at": dsl.workflow.created_at,
            },
            "variables": [
                {
                    "name": var.name,
                    "type": var.type,
                    "required": var.required,
                    "default": var.default,
                    "description": var.description,
                }
                for var in dsl.variables
            ],
            "nodes": [
                {
                    "id": node.id,
                    "type": node.type,
                    "position": {"x": node.position.x, "y": node.position.y},
                    "data": node.data,
                }
                for node in dsl.nodes
            ],
            "edges": [
                {
                    "id": edge.id or f"{edge.source}-{edge.target}",
                    "source": edge.source,
                    "target": edge.target,
                    "sourceHandle": edge.source_handle,
                    "targetHandle": edge.target_handle,
                    "type": edge.type,
                    "label": edge.label,
                }
                for edge in dsl.edges
            ],
        }

    def _dict_to_dsl(self, data: Dict[str, Any]) -> WorkflowDSL:
        """Convert dictionary to DSL model"""
        workflow_data = data.get("workflow", {})

        metadata = WorkflowMetadata(
            name=workflow_data.get("name", "Untitled Workflow"),
            description=workflow_data.get("description"),
            version=workflow_data.get("version", 1),
            version_tag=workflow_data.get("version_tag"),
            created_at=workflow_data.get("created_at"),
            updated_at=workflow_data.get("updated_at"),
            author=workflow_data.get("author"),
            tags=workflow_data.get("tags", []),
        )

        variables = []
        for var_data in data.get("variables", []):
            variables.append(VariableDefinition(
                name=var_data.get("name", ""),
                type=var_data.get("type", "string"),
                required=var_data.get("required", False),
                default=var_data.get("default"),
                description=var_data.get("description"),
            ))

        nodes = []
        for node_data in data.get("nodes", []):
            pos = node_data.get("position", {})
            nodes.append(NodeDefinition(
                id=node_data.get("id", str(uuid.uuid4())),
                type=node_data.get("type", "unknown"),
                position=NodePosition(
                    x=pos.get("x", 0),
                    y=pos.get("y", 0)
                ),
                data=node_data.get("data", {})
            ))

        edges = []
        for edge_data in data.get("edges", []):
            edges.append(EdgeDefinition(
                id=edge_data.get("id"),
                source=edge_data.get("source", ""),
                target=edge_data.get("target", ""),
                source_handle=edge_data.get("sourceHandle"),
                target_handle=edge_data.get("targetHandle"),
                type=edge_data.get("type", "default"),
                label=edge_data.get("label"),
            ))

        return WorkflowDSL(
            workflow=metadata,
            variables=variables,
            nodes=nodes,
            edges=edges
        )

    def _dsl_to_spec(self, dsl: WorkflowDSL) -> Dict[str, Any]:
        """Convert DSL model to React Flow spec"""
        nodes = []
        for node in dsl.nodes:
            nodes.append({
                "id": node.id,
                "type": node.type,
                "position": {
                    "x": node.position.x,
                    "y": node.position.y
                },
                "data": node.data,
            })

        edges = []
        for edge in dsl.edges:
            edge_dict = {
                "id": edge.id or f"{edge.source}-{edge.target}",
                "source": edge.source,
                "target": edge.target,
                "type": edge.type,
            }
            if edge.source_handle:
                edge_dict["sourceHandle"] = edge.source_handle
            if edge.target_handle:
                edge_dict["targetHandle"] = edge.target_handle
            if edge.label:
                edge_dict["label"] = edge.label
            edges.append(edge_dict)

        # Include variables in spec
        variables = []
        for var in dsl.variables:
            variables.append({
                "name": var.name,
                "type": var.type,
                "required": var.required,
                "default": var.default,
                "description": var.description,
            })

        return {
            "nodes": nodes,
            "edges": edges,
            "variables": variables,
        }


# Singleton instance
workflow_dsl_service = WorkflowDSLService()
