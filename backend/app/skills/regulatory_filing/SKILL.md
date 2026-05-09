---
name: regulatory_filing
description: Parse and retrieve SEC/FINRA filings for an entity.

type: tool
category: compliance

tool:
  module: app.skills.regulatory_filing.tool
  function: regulatory_filing
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Regulatory Filing

Get regulatory filings for a company.

## Usage

```python
result = await regulatory_filing(cik="0001018724", filing_type="10-K")
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `cik` | string | Yes | - | SEC CIK number |
| `filing_type` | string | No | "10-K" | Filing type (10-K, 10-Q, 8-K) |

## Response

```json
{
  "cik": "0001018724",
  "filing_type": "10-K",
  "latest_filing_date": "2023-10-27",
  "url": "https://www.sec.gov/...",
  "summary": "Annual report summary..."
}
```
