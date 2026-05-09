
"""
Skills Performance Benchmarks
Measures load time, execution latency, and memory overhead.
"""

from typing import Dict
import time
import tracemalloc
import asyncio
import statistics
import sys
import os
import gc

# Ensure backend path is in pythonpath
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../..")))

from app.agents.skills.registry import SkillRegistry
from app.tests.skills.runner import SkillTest, run_skill_test

async def measure_load_time(iterations: int = 20) -> float:
    """
    Measure average time to load a skill (cold start simulation)
    Note: Python caches imports, so true cold start is hard to simulate repeatedly 
    in one process without careful unloading. We will simulate registry lookup overhead.
    """
    times = []
    
    # We can't easily unload modules safely, but we can measure the registry's retrieval logic
    # including the importlib call if we target different skills or just measure the overhead
    # of the first call vs subsequent.
    
    # For this benchmark, we'll measure the time to get_skill for 'execute_python'
    # The first time will be the "load" time.
    
    # Clear registry cache to simulate fresh lazy load trigger (if possible)
    # The registry uses importlib.import_module, which is cached by sys.modules.
    # So subsequent calls are fast. We will measure P95 of the *first* call in a fresh run normally,
    # but here we just measure the registry lookup speed.
    
    start = time.perf_counter()
    _ = SkillRegistry.get_skill("execute_python")
    end = time.perf_counter()
    return (end - start) * 1000  # ms


async def measure_execution_latency(iterations: int = 50) -> Dict[str, float]:
    """
    Measure execution latency statistics
    """
    test_case = SkillTest(
        skill_name="execute_python",
        description="Benchmark calc",
        inputs={"code": "result = 1 + 1"},
        expected_output=None
    )
    
    latencies = []
    
    for _ in range(iterations):
        start = time.perf_counter()
        await run_skill_test(test_case)
        end = time.perf_counter()
        latencies.append((end - start) * 1000)  # ms
        
    return {
        "min": min(latencies),
        "max": max(latencies),
        "avg": statistics.mean(latencies),
        "p95": statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies)
    }

def measure_memory_overhead():
    """
    Measure memory overhead of loading skills
    """
    gc.collect()
    tracemalloc.start()
    
    snapshot1 = tracemalloc.take_snapshot()
    
    # Load all built-in skills
    registry = SkillRegistry()
    skills = registry.get_all_skills()
    
    # Force load of handlers
    for name, skill in skills.items():
        # Accessing handler property or using get_skill ensures import
        _ = registry.get_skill(name) 
        
    snapshot2 = tracemalloc.take_snapshot()
    tracemalloc.stop()
    
    stats = snapshot2.compare_to(snapshot1, 'lineno')
    total_diff = sum(stat.size_diff for stat in stats)
    
    return total_diff / 1024  # KB


async def run_benchmarks():
    print("🚀 Running Skills Performance Benchmarks...\n")
    
    # 1. Load Time
    print("Running Load Time Benchmark...")
    load_time = await measure_load_time()
    print(f"Load Time (execute_python): {load_time:.2f} ms")
    
    # 2. Execution Latency
    print("\nRunning Execution Latency Benchmark (50 iter)...")
    original_stdout = sys.stdout
    sys.stdout = open(os.devnull, 'w') # Suppress runner logs
    latency_stats = await measure_execution_latency(50)
    sys.stdout = original_stdout
    
    print(f"Latency P95: {latency_stats['p95']:.2f} ms")
    print(f"Latency Avg: {latency_stats['avg']:.2f} ms")
    print(f"Latency Min: {latency_stats['min']:.2f} ms")
    print(f"Latency Max: {latency_stats['max']:.2f} ms")
    
    # 3. Memory Overhead
    print("\nRunning Memory Overhead Benchmark...")
    mem_overhead = measure_memory_overhead()
    print(f"Memory Overhead (All Skills): {mem_overhead:.2f} KB")

    print("\n✅ Benchmarks Complete")

if __name__ == "__main__":
    from typing import Dict
    asyncio.run(run_benchmarks())
