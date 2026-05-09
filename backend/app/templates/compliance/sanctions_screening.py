"""
Sanctions Screening Workflow Template
Real-time screening against global sanctions lists
"""

SANCTIONS_SCREENING_TEMPLATE = {
    "id": "sanctions-screening-v1",
    "name": "Sanctions Screening",
    "description": "Real-time sanctions screening against OFAC, UN, EU, and UK sanctions lists. Supports batch processing and continuous monitoring.",
    "version": "1.0.0",
    "category": "compliance",
    "regulatory_regimes": ["OFAC", "UN", "EU", "UK_SANCTIONS"],
    "estimated_duration_seconds": 10,
    "tags": ["sanctions", "ofac", "screening", "real-time"],

    "spec": {
        "nodes": [
            {
                "id": "start-1",
                "type": "start",
                "position": {"x": 100, "y": 200},
                "data": {
                    "label": "Start Sanctions Check",
                    "variables": [
                        {"name": "entity_name", "type": "string", "required": True},
                        {"name": "entity_type", "type": "string", "required": False, "default": "individual"},
                        {"name": "date_of_birth", "type": "string", "required": False},
                        {"name": "country", "type": "string", "required": False},
                        {"name": "aliases", "type": "array", "required": False},
                    ]
                }
            },
            {
                "id": "sanctions-check-1",
                "type": "compliance",
                "position": {"x": 400, "y": 200},
                "data": {
                    "label": "Multi-List Screening",
                    "compliance_type": "sanctions_screening",
                    "config": {
                        "databases": ["OFAC_SDN", "OFAC_CONS", "UN_SANCTIONS", "EU_SANCTIONS", "UK_SANCTIONS"],
                        "match_threshold": 0.85,
                        "fuzzy_matching": True,
                        "check_aliases": True,
                        "fields_to_check": ["entity_name", "date_of_birth", "country", "aliases"]
                    }
                }
            },
            {
                "id": "condition-1",
                "type": "ifelse",
                "position": {"x": 700, "y": 200},
                "data": {
                    "label": "Match Found?",
                    "conditions": [
                        {
                            "id": "match",
                            "variable": "{{sanctions-check-1.match_found}}",
                            "operator": "equals",
                            "value": True
                        }
                    ]
                }
            },
            {
                "id": "alert-generation-1",
                "type": "llm",
                "position": {"x": 1000, "y": 100},
                "data": {
                    "label": "Generate Alert",
                    "model": "anthropic/claude-3.5-sonnet",
                    "system_prompt": "You are a compliance analyst. Generate a concise sanctions alert for review.",
                    "prompt": """A potential sanctions match was found:

Entity: {{entity_name}}
Match Details: {{sanctions-check-1.matches}}

Generate a structured alert including:
1. Alert severity (HIGH/CRITICAL)
2. Summary of the match
3. Recommended immediate actions
4. Required documentation for false positive review""",
                    "temperature": 0.1
                }
            },
            {
                "id": "output-match",
                "type": "output",
                "position": {"x": 1300, "y": 100},
                "data": {
                    "label": "Match Result",
                    "output_type": "sanctions_alert"
                }
            },
            {
                "id": "output-clear",
                "type": "output",
                "position": {"x": 1000, "y": 300},
                "data": {
                    "label": "Clear Result",
                    "output_type": "sanctions_clear"
                }
            }
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "sanctions-check-1"},
            {"id": "e2", "source": "sanctions-check-1", "target": "condition-1"},
            {"id": "e3", "source": "condition-1", "target": "alert-generation-1", "sourceHandle": "match"},
            {"id": "e4", "source": "condition-1", "target": "output-clear", "sourceHandle": "else"},
            {"id": "e5", "source": "alert-generation-1", "target": "output-match"}
        ]
    },

    "sample_input": {
        "entity_name": "John Smith",
        "entity_type": "individual",
        "date_of_birth": "1975-03-15",
        "country": "United States",
        "aliases": []
    }
}
