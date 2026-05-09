---
name: browser_navigate
description: Navigate to a URL in the sandbox browser and extract page content using Browser Use.
type: tool
category: web
tool:
  module: app.skills.browser_navigate.tool
  function: browser_navigate
  async: true
metadata:
  author: oniva
  version: "1.0.0"
audit-level: basic
feature-flag: ENABLE_BROWSER_SANDBOX
---

# browser_navigate

Navigate to a URL using a headless Chromium browser running inside the E2B sandbox. Extracts visible text content from JavaScript-rendered pages.

## When to Use

- Pages that require JavaScript rendering (SPAs, dynamic content)
- Sites that block simple HTTP requests
- When you need to interact with a page before extracting content

## Parameters

| Parameter | Type   | Required | Description               |
|-----------|--------|----------|---------------------------|
| url       | string | yes      | URL to navigate to        |
| task      | string | no       | Extraction instruction for Browser Use agent |

## Returns

```json
{
  "url": "https://example.com",
  "status": "success",
  "content": "Extracted page text...",
  "title": "Page Title"
}
```
