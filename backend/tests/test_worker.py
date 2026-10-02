"""Worker startup owns the data directory before any broker consumption."""

import os
import select
import subprocess
import sys

import pytest

from privframe.config import ROOT


WORKER_SCRIPT = """
import sys
import chromadb
from privframe.worker import celery_app

def forbidden(*args, **kwargs):
    raise AssertionError('Worker startup must not initialize Chroma')
chromadb.PersistentClient = forbidden
worker = celery_app.Worker(pool=sys.argv[1], concurrency=int(sys.argv[2]),
                           hostname='startup-test@localhost', loglevel='ERROR')
print('STARTUP_OWNED', flush=True)
sys.stdin.readline()
worker.stop()
"""


def worker_env(directory):
    return {**os.environ, "PRIVFRAME_DATA_DIR": str(directory),
            "PRIVFRAME_REDIS_URL": "redis://127.0.0.1:1/0"}


@pytest.mark.parametrize("pool,concurrency", [("prefork", 1), ("threads", 1), ("solo", 2)])
def test_unsupported_pool_or_concurrency_is_rejected_before_startup(tmp_path, pool, concurrency):
    result = subprocess.run([sys.executable, "-c", WORKER_SCRIPT, pool, str(concurrency)],
                            env=worker_env(tmp_path / "data"), cwd=ROOT, input="\n",
                            capture_output=True, text=True, timeout=15)
    assert result.returncode != 0, result.stdout
    assert "STARTUP_OWNED" not in result.stdout
    assert "solo" in result.stderr.lower()


def test_second_worker_rejected_before_consuming_and_owner_release_allows_restart(tmp_path):
    command = [sys.executable, "-c", WORKER_SCRIPT, "solo", "1"]
    env = worker_env(tmp_path / "data")
    owner = subprocess.Popen(command, env=env, cwd=ROOT, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready, _, _ = select.select([owner.stdout], [], [], 15)
        assert ready, "Worker constructor did not finish before any broker connection"
        first_line = owner.stdout.readline().strip()
        assert first_line == "STARTUP_OWNED", first_line
        assert (tmp_path / "data/worker.lock").is_file()
        duplicate = subprocess.run(command, env=env, cwd=ROOT, input="\n",
                                   capture_output=True, text=True, timeout=15)
        assert duplicate.returncode != 0 and "STARTUP_OWNED" not in duplicate.stdout
        assert "another worker" in duplicate.stderr.lower()
        # No Redis exists at this address: success proves the guard runs while
        # constructing the worker, before starting or consuming from a broker.
        _, errors = owner.communicate("\n", timeout=15)
        assert owner.returncode == 0, errors
        restarted = subprocess.run(command, env=env, cwd=ROOT, input="\n",
                                   capture_output=True, text=True, timeout=15)
        assert restarted.returncode == 0, restarted.stderr
        assert "STARTUP_OWNED" in restarted.stdout
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.communicate(timeout=5)


def test_storage_initialization_failure_stops_worker_startup(tmp_path):
    invalid_directory = tmp_path / "data-is-a-file"
    invalid_directory.write_text("A configured file cannot be a data directory")
    result = subprocess.run([sys.executable, "-c", WORKER_SCRIPT, "solo", "1"],
                            env=worker_env(invalid_directory), cwd=ROOT, input="\n",
                            capture_output=True, text=True, timeout=15)
    assert result.returncode != 0, "Storage readiness failures must not be swallowed by worker_init"
    assert "STARTUP_OWNED" not in result.stdout
