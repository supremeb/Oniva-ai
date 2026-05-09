---
name: parse_pdf
description: Extract text and metadata from a PDF file.

type: tool
category: document

tool:
  module: app.skills.parse_pdf.tool
  function: parse_pdf
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Parse PDF

Extract text and metadata from PDF documents.

## Usage

```python
result = await parse_pdf(path="/documents/report.pdf", max_pages=10)
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | - | Path to the PDF file |
| `max_pages` | int | No | 10 | Maximum pages to extract text from |

## Response

```json
{
  "success": true,
  "metadata": {
    "title": "Annual Report 2024",
    "author": "Financial Corp",
    "pages": 45
  },
  "pages_extracted": 10,
  "text": "Full extracted text content...",
  "truncated": false
}
```

## Error Response

```json
{
  "success": false,
  "error": "File not found: /path/to/file.pdf"
}
```
