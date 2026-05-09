---
name: list_workspace
description: List files and directories in the agent's session workspace with metadata.

type: tool
category: file

tool:
  module: app.skills.list_workspace.tool
  function: list_workspace
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# List Workspace

List files and directories in the agent's session workspace. Returns metadata for each entry (name, type, size, modified timestamp). Lists immediate children only (non-recursive).

## Security

- All paths are relative to `workspace/` within the session directory
- Absolute paths and path traversal (`..`) are rejected
- Session-scoped: requires an active session_id
- Hardcoded cap of 50 entries

## Usage

```python
result = await list_workspace(path="analysis/")
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | No | "" | Subdirectory to list (default: workspace root) |

## Response

```json
{
  "success": true,
  "files": [
    {"name": "results.json", "type": "file", "size": 1234, "modified": "2026-02-13T10:30:00"},
    {"name": "charts", "type": "directory", "modified": "2026-02-13T10:25:00"}
  ],
  "total_count": 2,
  "truncated": false
}
```
