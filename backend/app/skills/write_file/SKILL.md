---
name: write_file
description: Write text content to the agent's session workspace for persistent storage across tool calls.

type: tool
category: file

tool:
  module: app.skills.write_file.tool
  function: write_file
  async: true

metadata:
  author: oniva
  version: "2.0.0"

audit-level: basic
---

# Write File

Write text content to the agent's session workspace. Files persist across tool calls within the same session and are shared between agents in multi-agent workflows.

## Security

- All paths are relative to `workspace/` within the session directory
- Absolute paths and path traversal (`..`) are rejected
- 10MB per-file size limit enforced
- Session-scoped: requires an active session_id

## Usage

```python
result = await write_file(file_path="analysis/results.json", content='{"score": 0.85}')
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | string | Yes | - | Relative path within workspace |
| `content` | string | Yes | - | Text content to write |

## Response

```json
{
  "success": true,
  "path": "analysis/results.json",
  "size_bytes": 18
}
```
