"""Background worker: polls the DB queue and executes pending task runs."""

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MAX_WORKERS = int(os.environ.get("HARNESS_MAX_WORKERS", "3"))
POLL_INTERVAL = 2       # seconds between queue polls
IDLE_EXIT_AFTER = 10    # exit after this many consecutive empty polls (~20s)
PID_FILE = Path.home() / ".agentic_harness" / "worker.pid"


# ── Worker process management ────────────────────────────────────────────────


def _is_running() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # signal 0 = "does this process exist?"
        return True
    except (ProcessLookupError, ValueError, OSError):
        return False


def ensure_worker_running() -> None:
    """Spawn the worker as a detached background process if not already running."""
    if _is_running():
        return

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [sys.executable, "-m", "agentic_harness.worker"],
        start_new_session=True,   # detach from the calling terminal
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    PID_FILE.write_text(str(proc.pid))


# ── Worker main loop ─────────────────────────────────────────────────────────


def run_worker() -> None:
    """Main loop: pull pending tasks from DB and execute them concurrently."""
    # Write our own PID so ensure_worker_running can detect us
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))

    try:
        _loop()
    finally:
        PID_FILE.unlink(missing_ok=True)


def _loop() -> None:
    from . import db, runner  # local import to avoid circular deps at module load

    idle_polls = 0  # consecutive polls that found no work

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        active_futures = {}  # future -> task_run_id

        while True:
            # Reap completed futures
            done = [f for f in list(active_futures) if f.done()]
            for f in done:
                del active_futures[f]
                try:
                    f.result()
                except Exception:
                    pass  # errors are already persisted inside execute_task_run

            # How many slots are free?
            free_slots = MAX_WORKERS - len(active_futures)
            if free_slots > 0:
                pending = db.get_pending_task_runs(limit=free_slots)
                if pending:
                    idle_polls = 0
                    for task_run in pending:
                        run = db.get_run(task_run["run_id"])
                        if run is None:
                            continue
                        future = pool.submit(
                            runner.execute_task_run,
                            task_run["id"],
                            task_run["run_id"],
                            task_run["task_id"],
                            run["agent"],
                            run["benchmark"],
                        )
                        active_futures[future] = task_run["id"]
                elif not active_futures:
                    # No pending tasks and nothing in flight — increment idle counter
                    idle_polls += 1
                    if idle_polls >= IDLE_EXIT_AFTER:
                        return  # clean exit; PID file removed by run_worker's finally block

            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run_worker()
