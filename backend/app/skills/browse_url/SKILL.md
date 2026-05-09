---
name: browse_url
description: Fetch and read content from a webpage URL. Extracts text or returns raw HTML.

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
| `extract_text` | bool | No | True | Extract text content; if False, return raw HTML |

## Response

```json
{
  "url": "https://example.com",
  "status": 200,
  "title": "Example Domain",
  "content": "This domain is for use in illustrative examples...",
  "content_type": "text"
}
```

## Examples

### Extract text from page
```python
result = await browse_url(url="https://news.ycombinator.com")
print(result["content"])
```

### Get raw HTML
```python
result = await browse_url(url="https://example.com", extract_text=False)
print(result["content"])  # Raw HTML
```
