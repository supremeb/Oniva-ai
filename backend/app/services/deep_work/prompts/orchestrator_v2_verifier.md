## Verification step

After the last `dw_write_section` call (step 6) and before `output_agent` and `dw_finalize`, call `verifier` once. Give it a one-line prompt; it reads the report and the learnings itself, in a clean context, and answers `{unsupported_claims, missing_citations, broken_citations}`, each a list of `{section, ...}` items.

Fix what it reports with `dw_write_section` (rewrite the section: remove or soften an unsupported claim, add the missing citation, correct the broken one using the numbers `dw_get_learnings` shows). Do not call the verifier a second time. If the verifier returns an error, continue to the next step. Then go on with step 7 or step 8.
