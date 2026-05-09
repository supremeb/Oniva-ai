---
name: browser_screenshot
description: Take a screenshot of a URL in the sandbox browser and save to session workspace.
type: tool
category: web
tool:
  module: app.skills.browser_screenshot.tool
  function: browser_screenshot
  async: true
metadata:
  author: oniva
  version: "1.0.0"
audit-level: basic
feature-flag: ENABLE_BROWSER_SANDBOX
---

# browser_screenshot

Take a full-page screenshot of a URL using headless Chromium inside the E2B sandbox. Saves the screenshot to the session workspace as a PNG file.

## When to Use

- Capture visual state of a webpage for compliance or reporting
- Screenshot dynamic/JS-rendered pages
- Visual verification of web content

## Parameters

| Parameter | Type   | Required | Description                            |
|-----------|--------|----------|----------------------------------------|
| url       | string | yes      | URL to screenshot                      |
| full_page | bool   | no       | Capture full page (default: true)      |
| filename  | string | no       | Output filename (default: screenshot.png) |

## Returns

```json
{
  "url": "https://example.com",
  "status": "success",
  "file_path": "/workspace/screenshot.png",
  "size_bytes": 45230
}
```
