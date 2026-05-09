import uvicorn
import sys
import os
import multiprocessing

# Add current directory to path
sys.path.append(os.getcwd())

if __name__ == "__main__":
    try:
        # Calculate optimal workers (2-4 workers per core, max 8)
        workers = min(multiprocessing.cpu_count() * 2, 8)

        print(f"Starting server with {workers} workers...")
        print("Performance optimizations enabled:")
        print("  - HTTP keep-alive connections")
        print("  - Multiple worker processes")
        print("  - Request timeouts configured")
        print("  - Connection limits optimized")

        uvicorn.run(
            "app.main:app",
            host="0.0.0.0",
            port=8000,
            workers=workers,
            reload=False,  # Disable reload in production
            log_level="info",
            timeout_keep_alive=75,  # Keep connections alive for 75s
            limit_concurrency=1000,  # Max concurrent connections
            limit_max_requests=10000,  # Restart worker after 10k requests
            backlog=2048,  # Connection backlog
        )
    except Exception as e:
        print(f"Error: {e}")
