---
name: kyc_verification
description: Verify identity against KYC datasets and watchlists.

type: tool
category: compliance

tool:
  module: app.skills.kyc_verification.tool
  function: kyc_verification
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: full
---

# KYC Verification

Perform Know Your Customer (KYC) verification.

## Usage

```python
result = await kyc_verification(full_name="John Doe", dob="1990-01-15", country="USA")
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `full_name` | string | Yes | - | Full name to verify |
| `dob` | string | Yes | - | Date of birth |
| `country` | string | Yes | - | Country of residence |

## Response

```json
{
  "status": "verified",
  "risk_level": "low",
  "checks_passed": ["identity_match", "dob_match"]
}
```
