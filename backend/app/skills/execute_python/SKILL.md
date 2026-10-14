---
name: execute_python
description: Execute Python code in an E2B Firecracker sandbox with human review.

type: tool
category: code

tool:
  module: app.skills.execute_python.tool
  function: execute_python
  async: true

metadata:
  author: oniva
  version: "2.0.0"

audit-level: high
---

# Execute Python

Execute Python code in an E2B Firecracker microVM sandbox.

## Execution Modes

### E2B Sandbox (ENABLE_SANDBOX=True + E2B_API_KEY set)
- Code runs in Firecracker microVM with hypervisor isolation
- **Requires human review approval before execution**
- Timeout: 60 seconds
- Full Python environment (pandas, numpy, etc.)
- Network isolated by Firecracker
- Output files saved to workspace/code_outputs/

### Sandbox disabled (ENABLE_SANDBOX=False)
- The skill refuses with `success: false` and an explanatory error.
- Code is never executed inside the API process.

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `code` | string | Yes | - | Python code to execute |
| `input_files` | list[str] | No | [] | Workspace files to upload to sandbox |
| `timeout` | int | No | 60 | Max execution time in seconds |

## Response (E2B Mode)

```json
{
  "success": true,
  "status": "pending_review",
  "ticket_id": "uuid",
  "message": "Code submitted for human review."
}
```

## Response (sandbox disabled)

```json
{"success": false, "output": null, "error": "Code execution is unavailable: the sandbox is not enabled. ..."}
```

## Agent Guidance

- If code execution is rejected, modify the code to address the reviewer's concerns
- Do not retry the same code more than 2 times
- Use input_files to pass workspace data to the sandbox
- Output files are automatically saved to workspace/code_outputs/

## Migration from v1.x

- Timeout changed: 5s (in-process) -> 60s (E2B remote)
- New parameter: `input_files` for workspace file upload
- E2B mode returns `pending_review` status instead of immediate results
- The in-process fallback has been removed; ENABLE_SANDBOX=False now refuses
