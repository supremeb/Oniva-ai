# Known failures (U1 baseline, for U13 quarantine)

Command: `cd backend && uv run pytest -q -p no:cacheprovider` (Python 3.12.11, versions per `uv.lock`)
Result: **16 failed, 1256 passed, 6 skipped** (1278 collected, ~54s). Audit baseline on unpinned deps was 1206 passed / 13 failed.
No test was modified in U1. Note: pytest also collects `app/tests` and `examples/`.

**U8 note:** `pytest.ini` now sets `testpaths = tests`, so `app/tests/*` and `examples/*` are no longer collected by a bare `uv run pytest`. That removes 3 rows below (`app/tests/skills/test_document.py::...::test_parse_pdf`, both `examples/test_workflows.py` tests): the bare-run baseline is 13 failures from `tests/`. Those 3 still fail if run by explicit path.

| Test id | Likely cause |
|---|---|
| app/tests/skills/test_document.py::TestDocumentSkills::test_parse_pdf | patches `PdfReader`, which `app.agents.skills.document` no longer exposes |
| examples/test_workflows.py::test_simple_qa | needs a live server on localhost:8000 (not a unit test) |
| examples/test_workflows.py::test_code_generation | needs a live server on localhost:8000 (not a unit test) |
| tests/integration/test_chat_pipeline.py::TestFormatError::test_generic_error | `format_error` now sanitizes the message; test expects passthrough |
| tests/test_agents.py::test_analysis_agent_result_type | no API key configured (env-dependent) |
| tests/test_compliance_agents.py::test_kyc_agent_execution_mock | no API key configured (env-dependent) |
| tests/test_episodic_memory.py::TestEpisodicMemorySearch::test_recall_similar_returns_results | MagicMock rows fail pydantic validation (newer pydantic) |
| tests/test_llm_gateway.py::TestLLMGateway::test_failover_to_second_provider | failover/retry behaviour differs from test expectation |
| tests/test_observability_api.py::TestDashboardMetricsEndpoint::test_dashboard_metrics_transforms_response | handler called directly without `db`; gets a `Depends` object |
| tests/test_observability_api.py::TestDashboardMetricsEndpoint::test_dashboard_metrics_empty | same as above |
| tests/test_observability_api.py::TestTraceSearchEndpoint::test_trace_search_with_results | MagicMock LLM records fail pydantic validation |
| tests/test_observability_api.py::TestTraceSearchEndpoint::test_trace_search_null_execution_trace | same as above |
| tests/test_rag_upgrade.py::TestDocumentProcessorIntegration::test_process_document_uses_new_chunking | "no embedding API key" (env-dependent) |
| tests/test_saml_provider.py::TestSAMLAuthentication::test_authenticate_missing_saml_response | not yet diagnosed |
| tests/test_user_preferences.py::TestContextFormatting::test_format_for_context | not yet diagnosed |
| tests/test_workflow_tool_generator.py::TestGenerateWorkflowTools::test_two_start_variables | not yet diagnosed |

## U13 quarantine status

All 13 `tests/` rows above are now marked `@pytest.mark.xfail(strict=True, reason="KNOWN_FAILURES U13: ...")`. A strict xfail that starts passing fails CI, forcing removal of the marker (KTD6). Unit tier: 1782 passed, 17 skipped, 13 xfailed. The 3 rows outside `tests/` (`app/tests`, `examples/`) are not collected by CI.

Frontend: the 12 `tsc` errors were fixed (type casts only). Jest: `FlowEditorContextMenu.test.tsx` (2 tests: needs QueryClientProvider plus a store mock for `pendingNodes`) and `NodeContextMenu.test.tsx` "renders correctly" (category label "Financial Agents" no longer rendered) are quarantined with `it.failing`, which fails CI if they start passing.
