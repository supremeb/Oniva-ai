---
name: fetch_market_data
description: Fetch real-time market data (price, volume, change) for a ticker.

type: tool
category: financial

tool:
  module: app.skills.fetch_market_data.tool
  function: fetch_market_data
  async: true

mock: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Fetch Market Data

Fetch market data for a given ticker.

## Usage

```python
result = await fetch_market_data(ticker="AAPL", asset_type="stock")
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `ticker` | string | Yes | - | Ticker symbol |
| `asset_type` | string | No | "stock" | Type: stock, crypto, forex |

## Response

```json
{
  "ticker": "AAPL",
  "asset_type": "stock",
  "price": 175.50,
  "change_percent": 1.25,
  "volume": 50000000,
  "timestamp": "2024-01-15T12:00:00Z"
}
```
