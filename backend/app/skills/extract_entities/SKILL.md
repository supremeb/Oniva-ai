---
name: extract_entities
description: Extract financial entities (Dates, Amounts, Organizations) from text.

type: tool
category: document

tool:
  module: app.skills.extract_entities.tool
  function: extract_entities
  async: true

mock: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Extract Entities

Extract financial entities from text using pattern matching and heuristics.

## Usage

```python
result = await extract_entities(
    text="Payment of $10,000 received on 2024-03-15 from Acme Corp.",
    entity_types=["DATE", "MONEY", "ORG"]
)
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `text` | string | Yes | - | Text to extract entities from |
| `entity_types` | list | No | ["DATE", "MONEY"] | Entity types to extract |

## Supported Entity Types

- `DATE` - Dates in YYYY-MM-DD or MM/DD/YYYY format
- `MONEY` - Dollar amounts (e.g., $1,000.00)
- `ORG` - Organization names (requires NLP model)

## Response

```json
{
  "success": true,
  "entities": {
    "dates": ["2024-03-15"],
    "money": ["$10,000"],
    "organizations": ["Acme Corp"]
  }
}
```
