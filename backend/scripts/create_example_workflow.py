#!/usr/bin/env python3
"""
Create a complex example workflow via API.

This workflow demonstrates:
1. Start node with input variables
2. HTTP node calling Exa.ai API
3. Code node processing search results
4. LLM node using OpenRouter for analysis
5. Output node for final results
"""

import httpx
import json

API_URL = "http://localhost:8000/api/v1/workflows"

WORKFLOW_SPEC = {
    "name": "Financial Research with Exa.ai + OpenRouter",
    "description": "Complex example workflow: Web search via Exa.ai HTTP API, processed by Code node, analyzed by OpenRouter LLM",
    "spec": {
        "nodes": [
            {
                "id": "start_1",
                "type": "start",
                "position": {"x": 100, "y": 200},
                "data": {
                    "label": "Start",
                    "variables": [
                        {"name": "query", "type": "string"},
                        {"name": "exa_api_key", "type": "string"}
                    ]
                }
            },
            {
                "id": "http_exa",
                "type": "http",
                "position": {"x": 350, "y": 200},
                "data": {
                    "label": "Exa.ai Search",
                    "method": "POST",
                    "url": "https://api.exa.ai/search",
                    "headers": {
                        "Content-Type": "application/json",
                        "x-api-key": "{{exa_api_key}}"
                    },
                    "body": json.dumps({
                        "query": "{{query}}",
                        "type": "neural",
                        "useAutoprompt": True,
                        "numResults": 5,
                        "contents": {"text": {"maxCharacters": 1000}}
                    }),
                    "timeout": 30
                }
            },
            {
                "id": "code_process",
                "type": "code",
                "position": {"x": 600, "y": 200},
                "data": {
                    "label": "Process Results",
                    "language": "python",
                    "code": """import json

# Get the HTTP response from Exa.ai
http_result = http_exa.get("body", {}) if isinstance(http_exa, dict) else {}

# Extract search results
results = http_result.get("results", [])

# Format results for LLM analysis
formatted_results = []
for i, r in enumerate(results[:5], 1):
    title = r.get("title", "No title")
    url = r.get("url", "")
    text = r.get("text", "")[:500]
    formatted_results.append(f"{i}. {title}\\n   URL: {url}\\n   Summary: {text}...")

result = "\\n\\n".join(formatted_results) if formatted_results else "No results found"
""",
                    "timeout": 30
                }
            },
            {
                "id": "llm_analyze",
                "type": "llm",
                "position": {"x": 850, "y": 200},
                "data": {
                    "label": "Analyze with LLM",
                    "model": "google/gemini-2.0-flash-exp:free",
                    "systemPrompt": "You are a financial research analyst. Analyze the provided search results and provide a concise, insightful summary. Focus on key financial implications, trends, and actionable insights.",
                    "userPrompt": "Based on these search results about {{query}}:\n\n{{code_process.result}}\n\nProvide a comprehensive analysis with:\n1. Key findings\n2. Financial implications\n3. Recommended actions",
                    "temperature": 0.7,
                    "maxTokens": 2048
                }
            },
            {
                "id": "output_1",
                "type": "output",
                "position": {"x": 1100, "y": 200},
                "data": {
                    "label": "Research Report",
                    "outputType": "markdown"
                }
            }
        ],
        "edges": [
            {"id": "e1", "source": "start_1", "target": "http_exa"},
            {"id": "e2", "source": "http_exa", "target": "code_process"},
            {"id": "e3", "source": "code_process", "target": "llm_analyze"},
            {"id": "e4", "source": "llm_analyze", "target": "output_1"}
        ]
    }
}

# Second workflow: Simple LLM-only workflow for testing OpenRouter
SIMPLE_WORKFLOW = {
    "name": "Simple OpenRouter LLM Test",
    "description": "Simple workflow to test OpenRouter LLM integration with free models",
    "spec": {
        "nodes": [
            {
                "id": "start_1",
                "type": "start",
                "position": {"x": 100, "y": 200},
                "data": {
                    "label": "Start",
                    "variables": [
                        {"name": "question", "type": "string"}
                    ]
                }
            },
            {
                "id": "llm_1",
                "type": "llm",
                "position": {"x": 400, "y": 200},
                "data": {
                    "label": "OpenRouter LLM",
                    "model": "google/gemini-2.0-flash-exp:free",
                    "systemPrompt": "You are a helpful financial assistant. Provide clear, concise answers.",
                    "userPrompt": "{{question}}",
                    "temperature": 0.7,
                    "maxTokens": 1024
                }
            },
            {
                "id": "output_1",
                "type": "output",
                "position": {"x": 700, "y": 200},
                "data": {
                    "label": "Answer",
                    "outputType": "text"
                }
            }
        ],
        "edges": [
            {"id": "e1", "source": "start_1", "target": "llm_1"},
            {"id": "e2", "source": "llm_1", "target": "output_1"}
        ]
    }
}


def create_workflow(workflow_data: dict) -> dict:
    """Create a workflow via API"""
    headers = {
        "Content-Type": "application/json",
        "X-Tenant-ID": "11111111-1111-1111-1111-111111111111",
        "X-User-ID": "00000000-0000-0000-0000-000000000001"
    }

    response = httpx.post(API_URL, json=workflow_data, headers=headers, timeout=30.0)
    response.raise_for_status()
    return response.json()


def main():
    print("Creating example workflows...")

    # Create simple workflow first
    print("\n1. Creating Simple OpenRouter LLM Test workflow...")
    try:
        result = create_workflow(SIMPLE_WORKFLOW)
        print(f"   Created: {result['name']} (ID: {result['id']})")
    except Exception as e:
        print(f"   Error: {e}")

    # Create complex workflow
    print("\n2. Creating Financial Research workflow...")
    try:
        result = create_workflow(WORKFLOW_SPEC)
        print(f"   Created: {result['name']} (ID: {result['id']})")
    except Exception as e:
        print(f"   Error: {e}")

    print("\nDone! Visit http://localhost:3000/workflows to see the workflows.")


if __name__ == "__main__":
    main()
