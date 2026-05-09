---
name: summarize_document
description: Summarize a text document using LLM-based summarization.

type: tool
category: document

tool:
  module: app.skills.summarize_document.tool
  function: summarize_document
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Summarize Document

Summarize text documents using LLM-based anchored iterative summarization.

## Usage

```python
result = await summarize_document(
    text="Long document text...",
    max_length=500,
    style="concise"
)
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `text` | string | Yes | - | Text to summarize |
| `max_length` | int | No | 500 | Maximum summary length in words |
| `style` | string | No | "concise" | Summary style: concise, detailed, or bullet |

## Summary Styles

- `concise` - Brief, focused summary
- `detailed` - Comprehensive summary with key points
- `bullet` - Bullet-point format

## Response

```json
{
  "success": true,
  "summary": "The document discusses...",
  "original_length": 15000,
  "method": "llm_anchored_iterative"
}
```

## Methods

- `llm_anchored_iterative` - Used for documents >= 1000 words
- `llm_single_pass` - Used for shorter documents
- `extractive_fallback` - Used when LLM is unavailable
