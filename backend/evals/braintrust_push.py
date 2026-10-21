"""Optional push of a report to Braintrust (off by default, never needed by tests or the gates).

Reuses the existing integration's settings (`BRAINTRUST_API_KEY`, `BRAINTRUST_PROJECT`, see
`app/services/braintrust_observability.py`). With no key, or the `braintrust` package missing, the push is skipped
and says why; it never raises into the CLI. Callers pass `init` in tests so nothing touches the network.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PushResult:
    pushed: bool
    reason: str = ""
    records: int = 0


def push_report(
    report: dict[str, Any],
    *,
    api_key: str | None = None,
    project: str | None = None,
    init: Callable[..., Any] | None = None,
) -> PushResult:
    """Log one experiment record per task. `api_key` / `project` default to the app settings."""
    if api_key is None or project is None:
        from app.config import get_settings

        settings = get_settings()
        api_key = api_key if api_key is not None else settings.BRAINTRUST_API_KEY
        project = project or settings.BRAINTRUST_PROJECT
    if not api_key:
        return PushResult(False, "BRAINTRUST_API_KEY is not set")
    if init is None:
        try:
            from braintrust import init as bt_init
        except ImportError:
            return PushResult(False, "the braintrust package is not installed")
        init = bt_init
    try:
        experiment = init(project=project, experiment=report["name"], api_key=api_key)
        for tid, t in report["tasks"].items():
            experiment.log(
                input={"task_id": tid, "mode": report["mode"], "k": t["k"]},
                output={"passes": t["passes"], "trials": t["trials"]},
                scores={
                    key: float(t[key]) for key in ("pass_at_k", "pass_hat_k", "pass_rate") if t.get(key) is not None
                },
                metadata={
                    "cost_usd": t["cost_usd"],
                    "latency_p50_s": t["latency_p50_s"],
                    "latency_p95_s": t["latency_p95_s"],
                    "cache_read_share": t["cache_read_share"],
                    "compactions": t["compactions"],
                    "tool_calls": t["tool_calls"],
                },
            )
        experiment.flush()
    except Exception as e:  # noqa: BLE001 - observability must never fail an eval run
        return PushResult(False, f"push failed: {type(e).__name__}: {e}")
    return PushResult(True, records=len(report["tasks"]))
