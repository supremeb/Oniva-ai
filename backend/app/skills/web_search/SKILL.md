---
name: web_search
description: Search the web for information using Tavily or SerpAPI. Use when you need current information from the internet or need to research a topic.

type: tool
category: web

tool:
  module: app.skills.web_search.tool
  function: web_search
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Web Search

Search the web using configured search provider.

## Usage

```python
results = await web_search(query="latest AI news", num_results=5)
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | Yes | - | Search query |
| `num_results` | int | No | 5 | Number of results to return |

## Response

```json
{
  "query": "latest AI news",
  "results": [
    {
      "title": "Result title",
      "url": "https://example.com/article",
      "snippet": "Preview of the article content..."
    }
  ],
  "source": "tavily"
}
```

## Configuration

Requires one of:
- `TAVILY_API_KEY` - Tavily search API (recommended)
- `SERP_API_KEY` - SerpAPI search

If no API key is configured, returns mock results for testing.

## Examples

### Basic search
```python
results = await web_search(query="Python programming tutorials")
```

### Limited results
```python
results = await web_search(query="stock market news", num_results=3)
```
