---
name: risk_assessment
description: Calculate composite risk score for an entity using multiple factors.

type: tool
category: compliance

requires:
  - kyc_verification
  - sanctions_check

tool:
  module: app.skills.risk_assessment.tool
  function: risk_assessment
  async: true

mock: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: full
---

# Risk Assessment

Calculate composite risk score for an entity.

## Usage

```python
result = await risk_assessment(entity_name="Acme Corp", entity_type="corporation")
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entity_name` | string | Yes | - | Name of entity to assess |
| `entity_type` | string | Yes | - | Type: individual, corporation |

## Response

```json
{
  "entity": "Acme Corp",
  "type": "corporation",
  "composite_risk_score": 15,
  "rating": "Low Risk",
  "factors": {
    "geo_risk": "low",
    "industry_risk": "medium",
    "sanctions_risk": "none"
  }
}
```
