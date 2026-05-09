---
name: search_knowledge
description: Search the knowledge base using RAG (pgvector) semantic similarity.

type: tool
category: memory

tool:
  module: app.skills.search_knowledge.tool
  function: search_knowledge
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Search Knowledge

Search the knowledge base using semantic similarity.

## Usage

```python
result = await search_knowledge(query="financial regulations", limit=5)
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | Yes | - | Search query |
| `limit` | int | No | 5 | Maximum results |
| `threshold` | float | No | 0.01 | Minimum similarity (0-1) |

## Response

```json
{
  "success": true,
  "query": "financial regulations",
  "results": [
    {
      "content": "Relevant document chunk...",
      "source": "regulations.pdf",
      "similarity": 0.85,
      "chunk_id": "chunk_001"
    }
  ],
  "total": 1,
  "limit": 5,
  "threshold": 0.01
}
```
