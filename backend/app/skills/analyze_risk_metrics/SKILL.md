---
name: analyze_risk_metrics
description: Calculate risk metrics (VaR, Sharpe, volatility) given a set of returns.

type: tool
category: financial

tool:
  module: app.skills.analyze_risk_metrics.tool
  function: analyze_risk_metrics
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Analyze Risk Metrics

Calculate risk metrics from return data.

## Usage

```python
result = await analyze_risk_metrics(returns=[0.01, -0.02, 0.03, 0.01])
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `returns` | list[float] | Yes | - | List of returns |
| `benchmark_returns` | list[float] | No | None | Benchmark returns for beta |

## Response

```json
{
  "mean_return": 0.0075,
  "volatility": 0.0186,
  "sharpe_ratio": 0.403,
  "var_95": -0.0231
}
```
