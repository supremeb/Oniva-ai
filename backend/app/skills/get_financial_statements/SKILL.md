---
name: get_financial_statements
description: Get financial statements (income, balance, cash flow) for a company.

type: tool
category: financial

tool:
  module: app.skills.get_financial_statements.tool
  function: get_financial_statements
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Get Financial Statements

Get financial statements for a company.

## Usage

```python
result = await get_financial_statements(ticker="AAPL", statement_type="income")
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `ticker` | string | Yes | - | Ticker symbol |
| `statement_type` | string | No | "income" | Type: income, balance_sheet, cash_flow |
| `period` | string | No | "annual" | Period: annual, quarterly |

## Response

```json
{
  "ticker": "AAPL",
  "type": "income",
  "period": "annual",
  "data": {
    "revenue": 394328000000,
    "net_income": 99803000000,
    "year": 2023
  }
}
```
