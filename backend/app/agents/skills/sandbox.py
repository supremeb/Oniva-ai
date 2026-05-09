
import docker
import asyncio
from typing import Dict
import io
import tarfile

class SandboxExecutor:
    """
    Executes Python code in an isolated Docker container.
    """
    
    # Image to use for sandboxing
    SANDBOX_IMAGE = "python:3.12-slim"
    
    def __init__(self):
        try:
            self.client = docker.from_env()
        except docker.errors.DockerException:
            print("Warning: Docker not available. SandboxExecutor will fail if used.")
            self.client = None

    async def execute(
        self, 
        code: str, 
        network_enabled: bool = False, 
        timeout_seconds: int = 30,
        env_vars: Dict[str, str] = {}
    ) -> str:
        """
        Run code in a sandbox
        
        Args:
            code: Python code to execute
            network_enabled: Whether to allow internet access
            timeout_seconds: Max execution time
            env_vars: Environment variables to inject
            
        Returns:
            Combined stdout/stderr output
        """
        if not self.client:
             raise RuntimeError("Docker client not initialized. Is Docker running?")

        # Prepare the script
        # We wrap the user code to handle printing results or just execute it?
        # For now, let's assume the code prints the result or we just capture stdout.
        
        # Create a tar stream for the file to copy into container (to avoid mounting host paths if possible, 
        # reducing potential breakout vectors via mounts).
        script_content = code.encode('utf-8')
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode='w') as tar:
            tarinfo = tarfile.TarInfo(name='script.py')
            tarinfo.size = len(script_content)
            tar.addfile(tarinfo, io.BytesIO(script_content))
        tar_stream.seek(0)

        # Container Configuration
        network_mode = "bridge" if network_enabled else "none"
        
        try:
            # Create container (detached)
            container = self.client.containers.create(
                image=self.SANDBOX_IMAGE,
                command=["python", "/app/script.py"],
                working_dir="/app",
                network_mode=network_mode,
                mem_limit="128m",
                memswap_limit="128m",
                cpu_period=100000,
                cpu_quota=50000, # 0.5 CPU
                environment=env_vars,
                user="nobody", # Run as non-root
                read_only=True, # Read-only root fs
                tmpfs={'/tmp': '', '/var/tmp': ''}, # Writable tmp
            )
            
            # Put the script into the container
            container.put_archive("/app", tar_stream)
            
            # Start
            container.start()
            
            # Wait for completion (simulated async wait)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: container.wait(timeout=timeout_seconds))
            
            # Get logs
            logs = container.logs().decode('utf-8')
            
            exit_code = result.get('StatusCode', 0)
            if exit_code != 0:
                 return f"Error (Exit Code {exit_code}):\n{logs}"
            
            return logs

        except Exception as e:
            return f"Sandbox Error: {str(e)}"
        
        finally:
            # Cleanup
            try:
                if 'container' in locals():
                    container.remove(force=True)
            except Exception:
                pass
