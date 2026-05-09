#!/usr/bin/env python3
"""
Test workflow execution with example workflows
Run: python test_workflows.py
Requires: OPENROUTER_API_KEY or OPENAI_API_KEY environment variable
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# Add parent path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.node_executors import get_all_executors


async def test_simple_qa():
    """Test Simple Q&A workflow"""
    print("\n" + "="*60)
    print("TEST 1: Simple Q&A Workflow")
    print("="*60)
    
    with open("examples/workflows/01_simple_qa.json") as f:
        workflow = json.load(f)
    
    # Mock inputs
    inputs = {"sys.query": "What is the capital of France?"}
    
    print(f"Input: {inputs['sys.query']}")
    print("\nExecuting workflow...")
    
    # Execute via HTTP API
    import httpx
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "http://localhost:8000/api/v1/workflows/execute",
            json={
                "nodes": workflow["nodes"],
                "edges": workflow["edges"],
                "inputs": inputs
            }
        )
        
        if response.status_code == 200:
            result = response.json()
            print(f"Status: {result['status']}")
            print(f"Executed: {result['executed_nodes']}/{result['total_nodes']} nodes")
            print(f"\nResults:")
            for node_id, node_result in result["results"].items():
                print(f"  {node_id}: {node_result.get('status', 'unknown')}")
                if node_result.get("output"):
                    output = node_result["output"]
                    if isinstance(output, dict) and "text" in output:
                        print(f"    Output: {output['text'][:200]}...")
        else:
            print(f"Error: {response.status_code}")
            print(response.text)


async def test_code_generation():
    """Test Code Generation workflow"""
    print("\n" + "="*60)
    print("TEST 2: Code Generation Workflow")
    print("="*60)
    
    with open("examples/workflows/02_code_generation.json") as f:
        workflow = json.load(f)
    
    inputs = {"sys.query": "Write a Python function to calculate fibonacci numbers"}
    
    print(f"Input: {inputs['sys.query']}")
    print("\nExecuting workflow...")
    
    import httpx
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            "http://localhost:8000/api/v1/workflows/execute",
            json={
                "nodes": workflow["nodes"],
                "edges": workflow["edges"],
                "inputs": inputs
            }
        )
        
        if response.status_code == 200:
            result = response.json()
            print(f"Status: {result['status']}")
            print(f"Executed: {result['executed_nodes']}/{result['total_nodes']} nodes")
        else:
            print(f"Error: {response.status_code}")
            print(response.text)


async def main():
    """Run all tests"""
    print("\n" + "#"*60)
    print("# WORKFLOW EXECUTION TESTS")
    print("#"*60)
    
    # Check for API keys
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    
    if not openrouter_key and not openai_key:
        print("\n⚠️  Warning: No API keys found!")
        print("  Set OPENROUTER_API_KEY or OPENAI_API_KEY")
    else:
        key_type = "OpenRouter" if openrouter_key else "OpenAI"
        print(f"\n✓ Using {key_type} API key")
    
    # Show registered executors
    executors = get_all_executors()
    print(f"\n✓ Registered executors: {list(executors.keys())}")
    
    # Run tests
    try:
        await test_simple_qa()
        await test_code_generation()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "#"*60)
    print("# TESTS COMPLETE")
    print("#"*60)


if __name__ == "__main__":
    asyncio.run(main())
