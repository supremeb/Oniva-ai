---
name: store_decision
description: Store a decision to episodic memory for future recall.

type: tool
category: memory

tool:
  module: app.skills.store_decision.tool
  function: store_decision
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Store Decision

Store important decisions to episodic memory so future agent runs can learn from them.

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `agent_name` | string | Yes | - | Agent name (e.g., "analysis") |
| `action_type` | string | Yes | - | One of: analysis, compliance_check, recommendation, research, other |
| `context` | string | Yes | - | Situation description |
| `decision` | string | Yes | - | The decision made |
| `rationale` | string | No | None | Why the decision was made |
| `entities` | list[str] | No | None | Entities involved |
