---
name: read_file
description: Read text content from the agent's session workspace.

type: tool
category: file

tool:
  module: app.skills.read_file.tool
  function: read_file
  async: true

metadata:
  author: oniva
  version: "2.1.0"

audit-level: basic
---

# Read File

Read text content from the agent's session workspace. Files are scoped to the current session and shared between agents in multi-agent workflows.

## Security

- All paths are relative to `workspace/` within the session directory
- Absolute paths and path traversal (`..`) are rejected
- Session-scoped: requires an active session_id
- `tool_results/` and `conversation_history/` resolve against the root conversation (shared by an orchestrator and its workers), not the run, and only within the caller's tenant

## Usage

```python
result = await read_file(file_path="analysis/results.json")
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | string | Yes | - | Relative path within workspace, or `tool_results/<id>.txt` / `conversation_history/<id>.md` |
| `offset` | integer | No | 0 | First line to return (0-based) |
| `limit` | integer | No | whole file (200 lines / 20k chars for the two conversation prefixes) | Maximum lines to return |

## Response

```json
{
  "success": true,
  "content": "{\"score\": 0.85}",
  "size_bytes": 18
}
```

When paged (any file under the two conversation prefixes, or whenever `offset` or `limit` is passed) the response also carries `offset`, `total_lines` and `next_offset` (`null` at the end), plus a `note` saying how to continue. Calls with neither parameter on a workspace file return exactly the response above.
