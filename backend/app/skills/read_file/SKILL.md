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
  version: "2.0.0"

audit-level: basic
---

# Read File

Read text content from the agent's session workspace. Files are scoped to the current session and shared between agents in multi-agent workflows.

## Security

- All paths are relative to `workspace/` within the session directory
- Absolute paths and path traversal (`..`) are rejected
- Session-scoped: requires an active session_id

## Usage

```python
result = await read_file(file_path="analysis/results.json")
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | string | Yes | - | Relative path within workspace |

## Response

```json
{
  "success": true,
  "content": "{\"score\": 0.85}",
  "size_bytes": 18
}
```
