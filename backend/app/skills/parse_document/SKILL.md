---
name: parse_document
description: Parse structured content from documents (CSV, JSON, text).

type: tool
category: file

tool:
  module: app.skills.parse_document.tool
  function: parse_document
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Parse Document

Parse structured document content.

## Usage

```python
result = await parse_document(content='{"name": "test"}', format="json")
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `content` | string | Yes | - | Document content (string) |
| `format` | string | No | "auto" | Format: 'csv', 'json', 'auto' |

## Response (JSON)

```json
{
  "success": true,
  "format": "json",
  "data": {"name": "test"},
  "type": "dict"
}
```

## Response (CSV)

```json
{
  "success": true,
  "format": "csv",
  "columns": ["name", "value"],
  "rows": [{"name": "a", "value": "1"}],
  "total_rows": 1,
  "truncated": false
}
```

## Examples

### Parse JSON
```python
result = await parse_document(content='[1, 2, 3]')
```

### Parse CSV
```python
csv_data = "name,age\\nAlice,30\\nBob,25"
result = await parse_document(content=csv_data, format="csv")
```
