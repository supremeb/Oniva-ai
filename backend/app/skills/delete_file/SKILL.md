---
name: delete_file
description: Delete a file from the agent's session workspace.

type: tool
category: file

tool:
  module: app.skills.delete_file.tool
  function: delete_file
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Delete File

Delete a file from the agent's session workspace.

## Security

- All paths are relative to `workspace/` within the session directory
- Absolute paths and path traversal (`..`) are rejected
- Session-scoped: requires an active session_id

## Usage

```python
result = await delete_file(file_path="data/old_results.json")
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | string | Yes | - | Relative path within workspace |

## Response

```json
{
  "success": true,
  "deleted": true
}
```
