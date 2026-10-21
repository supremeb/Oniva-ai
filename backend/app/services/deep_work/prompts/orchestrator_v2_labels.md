## Subagent calls

- Pass `label` with every `research_worker` call, set to that worker's `worker_id` (for example `"label": "w1"`). The label is how you refer to a running worker later.
- For a gap round (`g1`, `g2`, ...), also pass `context` with a `brief` of at most 3000 characters: a short digest of what `dw_get_learnings` already holds on the gap's topic, so the worker does not repeat it.
