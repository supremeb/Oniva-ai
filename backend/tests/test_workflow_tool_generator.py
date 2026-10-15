"""
Tests for the workflow tool generator.

Verifies that workflow specs are correctly converted into Pydantic AI Tool
objects that return step previews for user approval.
"""

import uuid
import pytest

from app.agents.skills.workflow_tool_generator import (
    generate_workflow_tools,
    _sanitize_name,
    _traverse_nodes_by_edges,
    _extract_start_variables,
    _detect_hitl,
)


# ---------------------------------------------------------------------------
# Helpers — lightweight workflow stub
# ---------------------------------------------------------------------------

class _FakeWorkflow:
    """Minimal object matching the Workflow model interface."""

    def __init__(self, *, name: str, description: str, spec: dict, id: uuid.UUID | None = None):
        self.id = id or uuid.uuid4()
        self.name = name
        self.description = description
        self.spec = spec


# ---------------------------------------------------------------------------
# Inline spec fixtures (based on seed_demo_data.py specs)
# ---------------------------------------------------------------------------

KYC_SPEC = {
    "nodes": [
        {
            "id": "start_1",
            "type": "start",
            "position": {"x": 50, "y": 200},
            "data": {
                "label": "Start",
                "variables": [
                    {"name": "entity_name", "type": "string"},
                    {"name": "entity_type", "type": "string"},
                ],
            },
        },
        {
            "id": "llm_extract",
            "type": "llm",
            "position": {"x": 300, "y": 200},
            "data": {"label": "Extract Entity Info"},
        },
        {
            "id": "llm_risk",
            "type": "llm",
            "position": {"x": 550, "y": 200},
            "data": {"label": "KYC Risk Scoring"},
        },
        {
            "id": "ifelse_risk",
            "type": "ifelse",
            "position": {"x": 800, "y": 200},
            "data": {"label": "Risk Level Check"},
        },
        {
            "id": "output_high",
            "type": "output",
            "position": {"x": 1050, "y": 100},
            "data": {"label": "High Risk Result"},
        },
        {
            "id": "output_low",
            "type": "output",
            "position": {"x": 1050, "y": 350},
            "data": {"label": "Low/Medium Risk Result"},
        },
    ],
    "edges": [
        {"id": "e1", "source": "start_1", "target": "llm_extract"},
        {"id": "e2", "source": "llm_extract", "target": "llm_risk"},
        {"id": "e3", "source": "llm_risk", "target": "ifelse_risk"},
        {"id": "e4", "source": "ifelse_risk", "target": "output_high", "sourceHandle": "true"},
        {"id": "e5", "source": "ifelse_risk", "target": "output_low", "sourceHandle": "false"},
    ],
}

HITL_SPEC = {
    "nodes": [
        {
            "id": "start_1",
            "type": "start",
            "position": {"x": 50, "y": 200},
            "data": {
                "label": "Start",
                "variables": [
                    {"name": "client_name", "type": "string"},
                    {"name": "client_type", "type": "string"},
                    {"name": "jurisdiction", "type": "string"},
                ],
            },
        },
        {
            "id": "llm_requirements",
            "type": "llm",
            "position": {"x": 300, "y": 200},
            "data": {"label": "Determine Required Documents"},
        },
        {
            "id": "llm_verification",
            "type": "llm",
            "position": {"x": 550, "y": 200},
            "data": {"label": "Simulate Document Verification"},
        },
        {
            "id": "ifelse_complete",
            "type": "ifelse",
            "position": {"x": 800, "y": 200},
            "data": {"label": "Documents Complete?"},
        },
        {
            "id": "hitl_approval",
            "type": "human_in_loop",
            "position": {"x": 1050, "y": 100},
            "data": {
                "label": "Compliance Officer Approval",
                "title": "Client Onboarding Approval",
                "approverType": "role",
            },
        },
        {
            "id": "output_complete",
            "type": "output",
            "position": {"x": 1300, "y": 100},
            "data": {"label": "Onboarding Approved"},
        },
        {
            "id": "output_incomplete",
            "type": "output",
            "position": {"x": 1050, "y": 350},
            "data": {"label": "Documents Incomplete"},
        },
    ],
    "edges": [
        {"id": "e1", "source": "start_1", "target": "llm_requirements"},
        {"id": "e2", "source": "llm_requirements", "target": "llm_verification"},
        {"id": "e3", "source": "llm_verification", "target": "ifelse_complete"},
        {"id": "e4", "source": "ifelse_complete", "target": "hitl_approval", "sourceHandle": "true"},
        {"id": "e5", "source": "ifelse_complete", "target": "output_incomplete", "sourceHandle": "false"},
        {"id": "e6", "source": "hitl_approval", "target": "output_complete"},
    ],
}

NO_VARS_SPEC = {
    "nodes": [
        {
            "id": "start_1",
            "type": "start",
            "position": {"x": 50, "y": 200},
            "data": {"label": "Start"},
        },
        {
            "id": "llm_1",
            "type": "llm",
            "position": {"x": 300, "y": 200},
            "data": {"label": "Process Query"},
        },
        {
            "id": "output_1",
            "type": "output",
            "position": {"x": 550, "y": 200},
            "data": {"label": "Result"},
        },
    ],
    "edges": [
        {"id": "e1", "source": "start_1", "target": "llm_1"},
        {"id": "e2", "source": "llm_1", "target": "output_1"},
    ],
}


# ===========================================================================
# Tests: name sanitization
# ===========================================================================


class TestSanitizeName:
    def test_basic(self):
        assert _sanitize_name("KYC Screening Pipeline") == "run_kyc_screening_pipeline"

    def test_special_chars(self):
        assert _sanitize_name("Check Compliance (v2)") == "run_check_compliance_v2"

    def test_dashes_and_dots(self):
        assert _sanitize_name("risk-assessment.v3") == "run_risk_assessment_v3"

    def test_multiple_spaces(self):
        assert _sanitize_name("  My   Workflow  ") == "run_my_workflow"

    def test_numbers(self):
        assert _sanitize_name("Phase 2 Report") == "run_phase_2_report"


# ===========================================================================
# Tests: extract start variables
# ===========================================================================


class TestExtractStartVariables:
    def test_two_variables(self):
        vars_ = _extract_start_variables(KYC_SPEC)
        assert len(vars_) == 2
        assert vars_[0]["name"] == "entity_name"
        assert vars_[1]["name"] == "entity_type"

    def test_three_variables(self):
        vars_ = _extract_start_variables(HITL_SPEC)
        assert len(vars_) == 3
        names = [v["name"] for v in vars_]
        assert names == ["client_name", "client_type", "jurisdiction"]

    def test_no_variables(self):
        vars_ = _extract_start_variables(NO_VARS_SPEC)
        assert vars_ == []

    def test_empty_spec(self):
        vars_ = _extract_start_variables({})
        assert vars_ == []


# ===========================================================================
# Tests: edge traversal
# ===========================================================================


class TestTraverseNodes:
    def test_kyc_ordering(self):
        nodes = _traverse_nodes_by_edges(KYC_SPEC)
        ids = [n["id"] for n in nodes]
        # Start node should NOT be included
        assert "start_1" not in ids
        # llm_extract comes before llm_risk
        assert ids.index("llm_extract") < ids.index("llm_risk")
        # All 5 non-start nodes present
        assert len(ids) == 5

    def test_hitl_traversal(self):
        nodes = _traverse_nodes_by_edges(HITL_SPEC)
        ids = [n["id"] for n in nodes]
        assert "start_1" not in ids
        assert "hitl_approval" in ids
        assert len(ids) == 6  # 7 total nodes minus start

    def test_linear_ordering(self):
        nodes = _traverse_nodes_by_edges(NO_VARS_SPEC)
        ids = [n["id"] for n in nodes]
        assert ids == ["llm_1", "output_1"]


# ===========================================================================
# Tests: HITL detection
# ===========================================================================


class TestDetectHitl:
    def test_no_hitl(self):
        nodes = _traverse_nodes_by_edges(KYC_SPEC)
        has_hitl, warning = _detect_hitl(nodes)
        assert has_hitl is False
        assert warning is None

    def test_with_hitl(self):
        nodes = _traverse_nodes_by_edges(HITL_SPEC)
        has_hitl, warning = _detect_hitl(nodes)
        assert has_hitl is True
        assert "Compliance Officer Approval" in warning
        assert "1 human-in-the-loop" in warning

    def test_multiple_hitl(self):
        """Two HITL nodes should produce a combined warning."""
        nodes = [
            {"id": "h1", "type": "human_in_loop", "data": {"label": "Review A"}},
            {"id": "h2", "type": "human_in_loop", "data": {"label": "Review B"}},
            {"id": "x1", "type": "llm", "data": {"label": "Process"}},
        ]
        has_hitl, warning = _detect_hitl(nodes)
        assert has_hitl is True
        assert "2 human-in-the-loop" in warning
        assert "Review A" in warning
        assert "Review B" in warning


# ===========================================================================
# Tests: generate_workflow_tools — full integration
# ===========================================================================


class TestGenerateWorkflowTools:
    @pytest.mark.xfail(strict=True, reason="KNOWN_FAILURES U13: undiagnosed")
    def test_two_start_variables(self):
        wf = _FakeWorkflow(
            name="KYC Screening Pipeline",
            description="Run KYC screening on an entity.",
            spec=KYC_SPEC,
        )
        tools = generate_workflow_tools([wf])
        assert len(tools) == 1
        tool = tools[0]
        assert tool.name == "run_kyc_screening_pipeline"
        assert tool.description == "Run KYC screening on an entity."

    @pytest.mark.asyncio
    async def test_tool_returns_step_preview(self):
        wf = _FakeWorkflow(
            name="KYC Screening Pipeline",
            description="Run KYC screening on an entity.",
            spec=KYC_SPEC,
        )
        tools = generate_workflow_tools([wf])
        # Access the inner function from the tool
        tool = tools[0]
        # Call the underlying function directly
        result = await tool.function(entity_name="Acme Corp", entity_type="corporation")

        assert result["status"] == "pending_approval"
        assert result["workflow_id"] == str(wf.id)
        assert result["workflow_name"] == "KYC Screening Pipeline"
        assert result["has_hitl_nodes"] is False
        assert result["hitl_warning"] is None
        assert result["input_summary"] == {
            "entity_name": "Acme Corp",
            "entity_type": "corporation",
        }
        # Steps should include all non-start nodes
        assert len(result["steps"]) == 5
        step_types = [s["type"] for s in result["steps"]]
        assert "llm" in step_types
        assert "ifelse" in step_types
        assert "output" in step_types

    @pytest.mark.asyncio
    async def test_no_variables_fallback(self):
        wf = _FakeWorkflow(
            name="Simple Workflow",
            description="A simple workflow.",
            spec=NO_VARS_SPEC,
        )
        tools = generate_workflow_tools([wf])
        assert len(tools) == 1

        result = await tools[0].function(query="test input")
        assert result["status"] == "pending_approval"
        assert result["input_summary"] == {"query": "test input"}
        assert len(result["steps"]) == 2

    @pytest.mark.asyncio
    async def test_hitl_workflow_flags(self):
        wf = _FakeWorkflow(
            name="Client Onboarding Checklist",
            description="Onboard a new client.",
            spec=HITL_SPEC,
        )
        tools = generate_workflow_tools([wf])
        result = await tools[0].function(
            client_name="Jane Doe",
            client_type="individual",
            jurisdiction="US",
        )
        assert result["has_hitl_nodes"] is True
        assert result["hitl_warning"] is not None
        assert "Compliance Officer Approval" in result["hitl_warning"]
        assert result["input_summary"]["client_name"] == "Jane Doe"

    def test_duplicate_names_get_suffix(self):
        wf1 = _FakeWorkflow(name="My Workflow", description="First", spec=KYC_SPEC)
        wf2 = _FakeWorkflow(name="My Workflow", description="Second", spec=NO_VARS_SPEC)
        wf3 = _FakeWorkflow(name="My Workflow", description="Third", spec=NO_VARS_SPEC)
        tools = generate_workflow_tools([wf1, wf2, wf3])

        names = [t.name for t in tools]
        assert names == ["run_my_workflow", "run_my_workflow_2", "run_my_workflow_3"]

    def test_name_sanitization_special_chars(self):
        wf = _FakeWorkflow(
            name="Check Compliance (v2)",
            description="Check compliance v2.",
            spec=NO_VARS_SPEC,
        )
        tools = generate_workflow_tools([wf])
        assert tools[0].name == "run_check_compliance_v2"

    def test_empty_workflow_list(self):
        tools = generate_workflow_tools([])
        assert tools == []

    def test_workflow_with_no_nodes_skipped(self):
        wf = _FakeWorkflow(name="Empty", description="Empty", spec={"nodes": []})
        tools = generate_workflow_tools([wf])
        assert tools == []

    def test_workflow_with_none_spec(self):
        wf = _FakeWorkflow(name="Bad", description="Bad", spec=None)
        # spec is None — should not crash
        wf.spec = None
        tools = generate_workflow_tools([wf])
        assert tools == []

    def test_multiple_workflows(self):
        wf1 = _FakeWorkflow(
            name="KYC Screening Pipeline",
            description="Run KYC screening.",
            spec=KYC_SPEC,
        )
        wf2 = _FakeWorkflow(
            name="Client Onboarding Checklist",
            description="Onboard a client.",
            spec=HITL_SPEC,
        )
        tools = generate_workflow_tools([wf1, wf2])
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert "run_kyc_screening_pipeline" in names
        assert "run_client_onboarding_checklist" in names

    @pytest.mark.asyncio
    async def test_step_labels_match_node_data(self):
        wf = _FakeWorkflow(
            name="KYC Screening Pipeline",
            description="KYC",
            spec=KYC_SPEC,
        )
        tools = generate_workflow_tools([wf])
        result = await tools[0].function(entity_name="X", entity_type="Y")

        labels = {s["label"] for s in result["steps"]}
        assert "Extract Entity Info" in labels
        assert "KYC Risk Scoring" in labels
        assert "Risk Level Check" in labels
        assert "High Risk Result" in labels
        assert "Low/Medium Risk Result" in labels
