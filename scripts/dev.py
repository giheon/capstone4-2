"""Run native development processes and clean up their entire process groups."""
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def occupied(port):
    with socket.socket() as connection:
        return connection.connect_ex(("127.0.0.1", port)) == 0


def main():
    os.chdir(ROOT)
    if occupied(8000) or occupied(5173):
        raise SystemExit("Port 8000 or 5173 is already in use. Stop the previous development server first.")
    subprocess.run(["uv", "sync", "--frozen"], check=True)
    subprocess.run(["uv", "run", "python", "scripts/download_model.py"], check=True)
    subprocess.run(["uv", "run", "privframe", "export-openapi", "--output", "frontend/openapi.json"], check=True)
    subprocess.run(["npm", "ci"], cwd=ROOT / "frontend", check=True)
    subprocess.run(["npm", "run", "generate:api"], cwd=ROOT / "frontend", check=True)
    logs = ROOT / "data/logs"
    logs.mkdir(parents=True, exist_ok=True)
    (ROOT / "data/redis").mkdir(parents=True, exist_ok=True)
    commands = []
    if not occupied(6379):
        commands.append(("redis", ["redis-server", "--bind", "127.0.0.1", "--port", "6379", "--appendonly",
                                  "yes", "--dir", str(ROOT / "data/redis"), "--save", ""], ROOT))
    commands.extend([
        ("worker", ["uv", "run", "--no-sync", "celery", "-A", "privframe.worker:celery_app", "worker",
                    "--pool=solo", "--concurrency=1", "--loglevel=INFO"], ROOT),
        ("api", ["uv", "run", "--no-sync", "uvicorn", "privframe.api:app", "--host", "127.0.0.1",
                 "--port", "8000"], ROOT),
        ("frontend", ["npm", "run", "dev", "--", "--host", "127.0.0.1"], ROOT / "frontend"),
    ])
    processes, handles = [], []
    def cleanup(*_):
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        for handle in handles:
            handle.close()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        for name, command, cwd in commands:
            handle = (logs / f"{name}.log").open("a")
            handles.append(handle)
            processes.append(subprocess.Popen(command, cwd=cwd, stdout=handle, stderr=handle,
                                               start_new_session=True))
        print("PrivFrame: http://localhost:5173 · API: http://127.0.0.1:8000/docs", flush=True)
        print("Logs: data/logs/. Ctrl-C stops this session's processes.", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        raise SystemExit("A development process exited. Check data/logs/.")
    except KeyboardInterrupt:
        pass
    finally:
        cleanup()


if __name__ == "__main__":
    main()
