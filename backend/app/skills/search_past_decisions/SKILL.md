---
name: search_past_decisions
description: Search for similar past decisions using hybrid search.

type: tool
category: memory

tool:
  module: app.skills.search_past_decisions.tool
  function: search_past_decisions
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Search Past Decisions

Search episodic memory for similar past decisions before making new ones.

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | Yes | - | What to search for |
| `limit` | int | No | 3 | Maximum results |
| `search_mode` | string | No | hybrid | One of: hybrid, semantic, keyword |
| `agent_name` | string | No | None | Filter by agent |
| `action_type` | string | No | None | Filter by action type |
