---
name: get_workflow_entities
description: Get entities extracted from a specific workflow.

type: tool
category: memory

tool:
  module: app.skills.get_workflow_entities.tool
  function: get_workflow_entities
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Get Workflow Entities

Retrieve entities (companies, people, regulations) extracted from a workflow's outputs.

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `workflow_id` | string | Yes | - | Workflow UUID |
| `entity_type` | string | No | None | Filter by type |
| `limit` | int | No | 50 | Maximum results |
