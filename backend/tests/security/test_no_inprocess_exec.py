"""U3 / AE2: no code path executes model- or user-supplied code in the API process.

Code only runs through the E2B sandbox executor. With the sandbox disabled every
entry point returns a refusal and no process is spawned.
"""

import ast
import importlib
import importlib.util
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.skills.context import SkillContext
from app.node_executors import NodeExecutionContext

# Escape payload from code_tools_security.md: slipped past is_safe_code.
ESCAPE = "re.enum.sys.modules['subprocess'].check_output(['id'])"
ESCAPE_CODE = f"import re\nresult = {ESCAPE}"

APP_DIR = pathlib.Path(__file__).resolve().parents[2] / "app"


def _settings(enabled: bool):
    s = MagicMock()
    s.ENABLE_SANDBOX = enabled
    s.E2B_API_KEY = "test-e2b-key" if enabled else ""
    return s


@pytest.fixture
def no_spawn():
    """Fail loudly if anything tries to spawn a process."""
    with patch("subprocess.Popen") as popen, patch("os.system") as system:
        yield popen, system


@pytest.fixture
def sandbox_off():
    with patch("app.agents.skills.code.get_settings", return_value=_settings(False)), \
         patch("app.skills.execute_python.tool.get_settings", return_value=_settings(False)):
        yield


@pytest.fixture
def skill_ctx():
    return SkillContext(user_id="u1", tenant_id="t1", session_id="s1", db=MagicMock())


def _node_ctx(code: str) -> NodeExecutionContext:
    return NodeExecutionContext(
        node_id="n1", node_type="code", node_data={"code": code, "timeout": 5},
        inputs={}, variables={}, user_id="u1", tenant_id="t1", session_id="s1",
    )


# ---- AE2: each entry point refuses with the sandbox off -------------------

async def test_code_skill_execute_python_refuses(no_spawn, sandbox_off, skill_ctx):
    from app.agents.skills import code

    result = await code.execute_python(skill_ctx, ESCAPE_CODE)
    assert result["success"] is False
    assert "sandbox" in (result["error"] or "").lower()
    no_spawn[0].assert_not_called()
    no_spawn[1].assert_not_called()


async def test_workflow_code_node_refuses(no_spawn, sandbox_off):
    from app.node_executors.code_executor import CodeExecutor

    with patch("app.node_executors.code_executor.get_settings", return_value=_settings(False)):
        result = await CodeExecutor().execute(_node_ctx(ESCAPE_CODE), MagicMock())
    assert result.status == "failed"
    assert "sandbox" in (result.error or "").lower()
    no_spawn[0].assert_not_called()
    no_spawn[1].assert_not_called()


async def test_execute_python_skill_refuses_without_legacy_fallback(no_spawn, sandbox_off, skill_ctx):
    from app.skills.execute_python.tool import execute_python

    result = await execute_python(skill_ctx, ESCAPE_CODE)
    assert result["success"] is False
    assert "sandbox" in result["error"].lower()
    no_spawn[0].assert_not_called()
    no_spawn[1].assert_not_called()


def test_legacy_in_process_modules_are_gone():
    for name in ("app.skills.execute_python.tool_legacy", "app.agents.tools.bash_tool"):
        assert importlib.util.find_spec(name) is None, f"{name} must be deleted"


def test_code_skill_has_no_inprocess_exec_helpers():
    from app.agents.skills import code

    for attr in ("is_safe_code", "safe_import", "SAFE_BUILTINS"):
        assert not hasattr(code, attr), f"code.{attr} is part of the removed in-process sandbox"


# ---- Sandbox on: code is forwarded to the E2B executor --------------------

def _fake_executor(stdout="42", success=True, error=None):
    ex = MagicMock()
    ex.execute_with_lifecycle = AsyncMock(
        return_value=MagicMock(success=success, stdout=stdout, stderr="", error=error,
                               duration_seconds=0.1, output_files=None)
    )
    return ex


async def test_code_skill_forwards_to_e2b(skill_ctx):
    from app.agents.skills import code

    ex = _fake_executor()
    with patch("app.agents.skills.code.get_settings", return_value=_settings(True)), \
         patch("app.agents.skills.code.get_sandbox_executor", return_value=ex):
        result = await code.execute_python(skill_ctx, "print(42)")
    assert result["success"] is True
    assert result["output"] == "42"
    assert ex.execute_with_lifecycle.await_args.args[0] == "print(42)"


async def test_workflow_code_node_forwards_to_e2b():
    from app.node_executors.code_executor import CodeExecutor

    ex = _fake_executor(stdout="hello")
    with patch("app.agents.skills.code.get_settings", return_value=_settings(True)), \
         patch("app.node_executors.code_executor.get_settings", return_value=_settings(True)), \
         patch("app.agents.skills.code.get_sandbox_executor", return_value=ex):
        result = await CodeExecutor().execute(_node_ctx("print('hello')"), MagicMock())
    assert result.status == "success"
    assert result.output["stdout"] == "hello"
    assert "print('hello')" in ex.execute_with_lifecycle.await_args.args[0]


# ---- Grep guard: no exec/eval of non-literal input under app/ -------------

def _is_literal_str(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _offences():
    out = []
    for path in APP_DIR.rglob("*.py"):
        rel = path.relative_to(APP_DIR.parent)
        if "tests" in rel.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.id if isinstance(f, ast.Name) else None
            if name in {"exec", "eval"}:
                if not (node.args and _is_literal_str(node.args[0])):
                    out.append(f"{rel}:{node.lineno} {name}(<non-literal>)")
            elif isinstance(f, ast.Attribute) and f.attr in {"system", "popen"} \
                    and isinstance(f.value, ast.Name) and f.value.id == "os":
                out.append(f"{rel}:{node.lineno} os.{f.attr}(...)")
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                    and f.value.id == "subprocess":
                out.append(f"{rel}:{node.lineno} subprocess.{f.attr}(...)")
            elif name in {"create_subprocess_shell", "create_subprocess_exec"} or (
                isinstance(f, ast.Attribute) and f.attr in {"create_subprocess_shell", "create_subprocess_exec"}
            ):
                out.append(f"{rel}:{node.lineno} asyncio subprocess")
    return out


def test_no_exec_eval_or_subprocess_of_variable_input_under_app():
    offences = _offences()
    assert not offences, "In-process execution found:\n" + "\n".join(offences)


def test_grep_guard_detects_a_planted_exec(tmp_path):
    """The guard's detector must itself flag exec(variable)."""
    tree = ast.parse("def f(code):\n    exec(code)\n")
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert calls and not _is_literal_str(calls[0].args[0])
