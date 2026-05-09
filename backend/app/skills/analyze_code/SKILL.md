---
name: analyze_code
description: Analyze Python code for quality, complexity, and structural metrics.

type: tool
category: code

tool:
  module: app.skills.analyze_code.tool
  function: analyze_code
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Analyze Code

Analyze Python code quality and structure.

## Usage

```python
result = await analyze_code(code="def hello(): return 'world'")
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `code` | string | Yes | - | Python code to analyze |

## Response

```json
{
  "valid": true,
  "lines": 10,
  "functions": 2,
  "function_names": ["main", "helper"],
  "classes": 1,
  "class_names": ["MyClass"],
  "imports": 3,
  "complexity": 5,
  "complexity_rating": "low"
}
```

## Complexity Ratings

- **low**: complexity < 5
- **medium**: 5 <= complexity < 10
- **high**: complexity >= 10

## Examples

### Analyze a function
```python
code = '''
def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)
'''
result = await analyze_code(code=code)
print(f"Complexity: {result['complexity_rating']}")
```
