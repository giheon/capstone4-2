"""Run live browser checks against an isolated, automatically cleaned runtime."""
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def main():
    runtime = Path(tempfile.mkdtemp(prefix="privframe-live-"))
    api_port, redis_port = free_port(), free_port()
    env = {**os.environ, "PRIVFRAME_DATA_DIR": str(runtime / "data"),
           "PRIVFRAME_REDIS_URL": f"redis://127.0.0.1:{redis_port}/0",
           "PRIVFRAME_API_URL": f"http://127.0.0.1:{api_port}", "PRIVFRAME_LIVE_E2E": "1",
           "PRIVFRAME_TEST_OUTPUT": str(runtime / "browser")}
    commands = [
        ["redis-server", "--bind", "127.0.0.1", "--port", str(redis_port), "--save", "", "--appendonly", "no"],
        ["uv", "run", "--no-sync", "celery", "-A", "privframe.worker:celery_app", "worker", "--pool=solo", "--concurrency=1", "--loglevel=INFO"],
        ["uv", "run", "--no-sync", "uvicorn", "privframe.api:app", "--host", "127.0.0.1", "--port", str(api_port)],
    ]
    processes, handles = [], []
    success = False
    try:
        for index, command in enumerate(commands):
            log = (runtime / f"process-{index}.log").open("w")
            handles.append(log)
            processes.append(subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log, start_new_session=True))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                response = httpx.get(f"http://127.0.0.1:{api_port}/api/health", timeout=1)
                if response.is_success and response.json()["redis_available"]:
                    break
            except httpx.HTTPError:
                pass
            if any(process.poll() is not None for process in processes):
                raise RuntimeError(f"Live server startup failed; logs: {runtime}")
            time.sleep(0.2)
        else:
            raise TimeoutError("Live API startup timed out")
        result = subprocess.run(["npm", "run", "test:e2e", "--", "tests/live-workflow.spec.ts"],
                                cwd=ROOT / "frontend", env=env)
        success = result.returncode == 0
        if not success:
            raise SystemExit(result.returncode)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        for handle in handles:
            handle.close()
        if success:
            shutil.rmtree(runtime)
        else:
            print(f"Failed live run retained for inspection: {runtime}")


if __name__ == "__main__":
    main()
