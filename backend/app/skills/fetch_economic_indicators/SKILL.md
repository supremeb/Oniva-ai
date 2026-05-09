---
name: fetch_economic_indicators
description: Fetch economic indicators like GDP, CPI, Unemployment.

type: tool
category: financial

tool:
  module: app.skills.fetch_economic_indicators.tool
  function: fetch_economic_indicators
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Fetch Economic Indicators

Fetch economic indicators for a country.

## Usage

```python
result = await fetch_economic_indicators(country="USA", indicator="GDP")
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `country` | string | No | "USA" | Country code |
| `indicator` | string | No | "GDP" | Indicator: GDP, CPI, Unemployment |

## Response

```json
{
  "country": "USA",
  "indicator": "GDP",
  "value": 25000,
  "unit": "Billion USD"
}
```
