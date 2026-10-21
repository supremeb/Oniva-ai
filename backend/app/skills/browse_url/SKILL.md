---
name: browse_url
description: Fetch and read content from a webpage URL. Returns readable text (never raw HTML), paged with offset.

type: tool
category: web

tool:
  module: app.skills.browse_url.tool
  function: browse_url
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Browse URL

Fetch webpage content and extract text.

## Usage

```python
result = await browse_url(url="https://example.com", extract_text=True)
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | Yes | - | URL to fetch |
| `extract_text` | bool | No | True | Kept for compatibility; raw HTML is never returned |
| `offset` | int | No | 0 | Character offset into the text, to read past a truncated page (`next_offset` in the response) |

## Response

```json
{
  "url": "https://example.com",
  "status": 200,
  "title": "Example Domain",
  "content": "This domain is for use in illustrative examples...",
  "content_type": "text",
  "total_chars": 1256,
  "offset": 0,
  "truncated": false
}
```

## Examples

### Extract text from page
```python
result = await browse_url(url="https://news.ycombinator.com")
print(result["content"])
```

### Read the next part of a long page
```python
result = await browse_url(url="https://example.com/long", offset=20000)
```
