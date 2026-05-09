---
name: get_trading_volume
description: Analyze trading volume trends for a ticker.

type: tool
category: financial

tool:
  module: app.skills.get_trading_volume.tool
  function: get_trading_volume
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Get Trading Volume

Get trading volume analysis for a ticker.

## Usage

```python
result = await get_trading_volume(ticker="AAPL", days=30)
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `ticker` | string | Yes | - | Ticker symbol |
| `days` | int | No | 30 | Number of days to analyze |

## Response

```json
{
  "ticker": "AAPL",
  "average_volume": 50000000,
  "trend": "up",
  "days": 30
}
```
