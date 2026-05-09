---
name: extract_structured_data
description: Extract structured data (tables, lists) from HTML content using CSS selectors.

type: tool
category: web

tool:
  module: app.skills.extract_structured_data.tool
  function: extract_structured_data
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Extract Structured Data

Parse HTML and extract tables and lists.

## Usage

```python
result = await extract_structured_data(html="<html>...</html>", selector="table.data")
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `html` | string | Yes | - | HTML content to parse |
| `selector` | string | No | None | CSS selector to focus on specific elements |

## Response

```json
{
  "tables": [
    [["Header1", "Header2"], ["Row1Col1", "Row1Col2"]]
  ],
  "lists": [
    ["Item 1", "Item 2", "Item 3"]
  ],
  "tables_count": 1,
  "lists_count": 1
}
```

## Examples

### Extract all tables
```python
result = await extract_structured_data(html=page_html)
for table in result["tables"]:
    print(table)
```

### Extract specific section
```python
result = await extract_structured_data(
    html=page_html,
    selector="div.financial-data"
)
```
