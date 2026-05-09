---
name: search_entities
description: Search for entities (companies, people, regulations) across workflows.

type: tool
category: memory

tool:
  module: app.skills.search_entities.tool
  function: search_entities
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Search Entities

Find which workflows have analyzed a specific company, person, or regulation.

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entity_name` | string | Yes | - | Entity to search for |
| `entity_type` | string | No | None | Filter by type (company, person, regulation) |
| `limit` | int | No | 20 | Maximum results |
