---
name: sanctions_check
description: Check entity against OFAC/EU sanctions lists for compliance screening. Use when verifying that an entity is not on any prohibited parties list.

type: tool
category: compliance

tool:
  module: app.skills.sanctions_check.tool
  function: sanctions_check
  async: true

mock: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: full
tenant-scope: organization
retention-years: 7
---

# Sanctions Check

Screen entities against OFAC, EU, and other sanctions lists.

## Usage

```python
result = await sanctions_check(entity_name="Acme Corporation")
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entity_name` | string | Yes | - | Name of entity to check |
| `entity_type` | string | No | "unknown" | Type of entity (individual, company, etc.) |

## Response

```json
{
  "entity": "Acme Corporation",
  "entity_type": "company",
  "is_sanctioned": false,
  "matches": [],
  "sources_checked": ["OFAC SDN", "EU Consolidated", "UN Security Council"],
  "checked_at": "2024-01-15T10:30:00Z"
}
```

### When Match Found

```json
{
  "entity": "Evil Corp",
  "entity_type": "company",
  "is_sanctioned": true,
  "matches": [
    {
      "list": "OFAC SDN",
      "match_score": 100,
      "matched_name": "EVIL CORP",
      "program": "CYBER2"
    }
  ],
  "sources_checked": ["OFAC SDN", "EU Consolidated", "UN Security Council"],
  "checked_at": "2024-01-15T10:30:00Z"
}
```

## Compliance Notes

- All checks are logged with full audit trail
- Results retained for 7 years per regulatory requirements
- Supports tenant-level configuration for jurisdiction-specific lists

## Examples

### Basic Check
```python
result = await sanctions_check(entity_name="John Smith")
if result["is_sanctioned"]:
    print("Entity is on sanctions list!")
```

### With Entity Type
```python
result = await sanctions_check(
    entity_name="Global Trade LLC",
    entity_type="company"
)
```
