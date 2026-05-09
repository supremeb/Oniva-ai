---
name: assess_query_complexity
description: Assess whether this query requires deep work. Call this FIRST before any other tools. Deep work is needed for multi-step research, comparative analysis, or deliverable generation. Default to needs_deep_work=False.

type: tool
category: routing

tool:
  module: app.skills.assess_query_complexity.tool
  function: assess_query_complexity
  async: true

metadata:
  author: oniva
  version: "1.0.0"

audit-level: basic
---

# Assess Query Complexity

Binary classification of whether a query needs deep work mode.

## Usage

```python
result = await assess_query_complexity(needs_deep_work=False, reasoning="Simple factual question")
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `needs_deep_work` | bool | Yes | - | Whether the query requires deep work |
| `reasoning` | str | Yes | - | Brief explanation of why or why not |

## When Deep Work IS Needed

- Multi-step research across multiple sources
- Comparison or analysis of 3+ entities
- Producing deliverables (reports, presentations, spreadsheets)
- Tasks that would take a human analyst 30+ minutes

## When Deep Work is NOT Needed

- Simple questions, definitions, greetings
- Single lookups (stock price, latest news)
- Questions about internal docs
