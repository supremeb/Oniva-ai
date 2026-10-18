"""U36 fakes: the scripted model and the counting tool target are the e2e harness's, not copies.

  * the scripted model is the harness script (``tests/e2e/harness.py`` merges the chat, Fleet, Deep Work and
    workflow rules): a chat turn ``STEER_NOW`` is one text-only request, an ``llm`` workflow node is one request
    (``workflow_script``), a Fleet run ``recovery`` is three (``fleet_script.TOKENS``).
  * the counting tool target is the backend's ``/e2e/http/<post|hold|flaky>/<tag>`` endpoint (``backend_app.py``),
    read back through ``workflow_helpers`` (``starts`` / ``dones``).

What is added here: the 10-node workflow, the arithmetic for how many model requests a run profile makes, and the
gates that must be open so nothing in the scripts blocks.
"""
from tests.e2e import fleet_script, workflow_script
from tests.e2e.harness import STEER_ANSWER
from tests.e2e.workflow_helpers import dones, edge, http_node, node, save_workflow, starts

HTTP_TAG = "load"
CHAT_TEXT = "STEER_NOW load sample"
CHAT_ANSWER = STEER_ANSWER
FLEET_SCENARIO = "recovery"
WAIT_SECONDS = 30
WORKFLOW_NODES = 10
CHAT_REQUESTS_PER_SAMPLE = 1
WORKFLOW_REQUESTS_PER_RUN = 1  # the one ``llm`` node
FLEET_REQUESTS_PER_RUN = len(fleet_script.TOKENS[FLEET_SCENARIO])


def load_workflow(w) -> str:
    """Save the 10-node workflow: template, http (counting target), llm (scripted model), one branch with a skipped
    side, a merge, one 30 s wait and an output. Only the true branch runs, so a run makes exactly one HTTP call."""
    nodes = [
        node("s", "start"),
        node("t", "template", template="load run {{inputs.n}}"),
        http_node(w, "h", "post", HTTP_TAG),
        node("l", "llm", userPrompt=workflow_script.LLM_MARKER, systemPrompt="Summarise."),
        node("c", "ifelse", conditions=[{"variable": "yes", "operator": "equals", "value": "yes"}]),
        node("a", "template", template="taken"),
        node("b", "template", template="skipped"),
        node("j", "merge"),
        node("w", "wait", waitType="duration", duration=WAIT_SECONDS, durationUnit="seconds"),
        node("o", "output"),
    ]
    assert len(nodes) == WORKFLOW_NODES
    edges = [edge("s", "t"), edge("t", "h"), edge("h", "l"), edge("l", "c"), edge("c", "a", "true"),
             edge("c", "b", "false"), edge("a", "j"), edge("b", "j"), edge("j", "w"), edge("w", "o")]
    return save_workflow(w, nodes, edges, name="load-10-nodes")


def open_all_gates(w) -> None:
    """Nothing may block: the HTTP target's gate, the fake search (``GATED`` queries) and the held write."""
    (w.dw_dir / "gate-all").write_text("open")
    w.dw.gate.write_text("open")
    w.gate.write_text("open")


def expected_model_requests(workflow_runs: int, fleet_runs: int, chat_requests: int) -> int:
    """How many model requests the scripts make, which is what the ledger must hold one row for each."""
    return (workflow_runs * WORKFLOW_REQUESTS_PER_RUN + fleet_runs * FLEET_REQUESTS_PER_RUN
            + chat_requests * CHAT_REQUESTS_PER_SAMPLE)


def http_calls(w) -> tuple[int, int]:
    """(started, done) calls the counting target logged for the workflow's HTTP node."""
    return starts(w, HTTP_TAG), dones(w, HTTP_TAG)
