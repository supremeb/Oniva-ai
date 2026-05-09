import asyncio
import sys
import os

# Ensure backend path is in pythonpath
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../..")))

from app.tests.skills.runner import SkillTest, run_suite

async def verify_framework():
    tests = [
        # Test 1: Python Execution Success
        SkillTest(
            skill_name="execute_python",
            description="Simple python math calculation",
            inputs={
                "code": "result = 5 + 7"
            },
            expected_output={
                "success": True,
                "output": None,  # No print output
                "result": "12",
                "errors": None,
                "error": None
            }
        ),
        
        # Test 2: Python Execution Error (Unsafe)
        SkillTest(
            skill_name="execute_python",
            description="Unsafe import attempt",
            inputs={
                "code": "import os"
            },
            # Note: We rely on the tool's specific error return format
            expected_output={
                "success": False,
                "error": "Import not allowed: os",
                "output": None
            }
        ),
        
        # Test 3: Math import allowed
        SkillTest(
            skill_name="execute_python",
            description="Math module usage",
            inputs={
                "code": "import math\nresult = math.sqrt(16)"
            },
            expected_output={
                "success": True,
                "output": None,
                "result": "4.0",
                "errors": None,
                "error": None
            }
        )
    ]
    
    results = await run_suite(tests)
    
    # Check overall success
    failed = [r for r in results if not r.success]
    if failed:
        print(f"\nFAILED: {len(failed)} tests failed.")
        sys.exit(1)
    else:
        print("\nSUCCESS: All framework tests passed.")

if __name__ == "__main__":
    asyncio.run(verify_framework())
