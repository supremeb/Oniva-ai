---
name: aml_screening
description: Screen transactions for money laundering patterns and risk indicators.

type: tool
category: compliance

tool:
  module: app.skills.aml_screening.tool
  function: aml_screening
  async: true

mock: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: full
---

# AML Screening

Screen transactions for Anti-Money Laundering (AML) risk.

## Usage

```python
result = await aml_screening(amount=15000, sender_country="USA", receiver_country="Mexico")
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `amount` | float | Yes | - | Transaction amount |
| `sender_country` | string | Yes | - | Sender's country |
| `receiver_country` | string | Yes | - | Receiver's country |

## Response

```json
{
  "risk_score": 50,
  "flags": ["large_transaction", "cross_border"],
  "action": "manual_review"
}
```
