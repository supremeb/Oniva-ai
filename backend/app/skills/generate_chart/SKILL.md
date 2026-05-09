---
name: generate_chart
description: Generate charts and visualizations from data using matplotlib.

type: tool
category: code

tool:
  module: app.skills.generate_chart.tool
  function: generate_chart
  async: true

requires:
  - matplotlib

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Generate Chart

Generate charts from data.

## Usage

```python
result = await generate_chart(
    data={"labels": ["A", "B", "C"], "values": [10, 20, 30]},
    chart_type="bar",
    title="Sales Data"
)
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `data` | dict | Yes | - | Data with 'labels' and 'values' arrays |
| `chart_type` | string | No | "bar" | Type: 'bar', 'line', 'pie' |
| `title` | string | No | "Chart" | Chart title |

## Response

```json
{
  "success": true,
  "chart_type": "bar",
  "image_base64": "iVBORw0KGgo...",
  "image_url": "data:image/png;base64,iVBORw0KGgo..."
}
```

## Examples

### Bar chart
```python
result = await generate_chart(
    data={"labels": ["Q1", "Q2", "Q3", "Q4"], "values": [100, 150, 120, 180]},
    chart_type="bar",
    title="Quarterly Revenue"
)
```

### Pie chart
```python
result = await generate_chart(
    data={"labels": ["Stocks", "Bonds", "Cash"], "values": [60, 30, 10]},
    chart_type="pie",
    title="Portfolio Allocation"
)
```
