"""SQLite database layer for run and task result persistence."""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".agentic_harness" / "harness.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    conn = _connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id          TEXT PRIMARY KEY,
            agent       TEXT NOT NULL,
            benchmark   TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS task_runs (
            id          TEXT PRIMARY KEY,
            run_id      TEXT NOT NULL,
            task_id     TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            passed      INTEGER,
            score       REAL,
            details     TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(id)
        );
    """)
    conn.commit()
    conn.close()


# ── Runs ────────────────────────────────────────────────────────────────────


def create_run(run_id: str, agent: str, benchmark: str, now: str) -> None:
    conn = _connect()
    conn.execute(
        "INSERT INTO runs (id, agent, benchmark, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'pending', ?, ?)",
        (run_id, agent, benchmark, now, now),
    )
    conn.commit()
    conn.close()


def get_run(run_id: str) -> sqlite3.Row | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    return row


def update_run_status(run_id: str, status: str, now: str) -> None:
    conn = _connect()
    conn.execute(
        "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
        (status, now, run_id),
    )
    conn.commit()
    conn.close()


def refresh_run_status(run_id: str, now: str) -> None:
    """Derive run status from its task_runs and update accordingly."""
    conn = _connect()
    rows = conn.execute(
        "SELECT status FROM task_runs WHERE run_id = ?", (run_id,)
    ).fetchall()
    statuses = {r["status"] for r in rows}

    if "running" in statuses:
        status = "running"
    elif statuses == {"completed"} or statuses == {"failed"} or statuses <= {"completed", "failed"}:
        status = "completed"
    elif "pending" in statuses:
        status = "running"
    else:
        status = "completed"

    conn.execute(
        "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
        (status, now, run_id),
    )
    conn.commit()
    conn.close()


# ── Task runs ────────────────────────────────────────────────────────────────


def create_task_run(task_run_id: str, run_id: str, task_id: str, now: str) -> None:
    conn = _connect()
    conn.execute(
        "INSERT INTO task_runs "
        "(id, run_id, task_id, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'pending', ?, ?)",
        (task_run_id, run_id, task_id, now, now),
    )
    conn.commit()
    conn.close()


def get_task_runs(run_id: str) -> list[sqlite3.Row]:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM task_runs WHERE run_id = ? ORDER BY created_at",
        (run_id,),
    ).fetchall()
    conn.close()
    return rows


def get_pending_task_runs(limit: int = 5) -> list[sqlite3.Row]:
    """Fetch and atomically claim pending task runs."""
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM task_runs WHERE status = 'pending' "
        "ORDER BY created_at LIMIT ?",
        (limit,),
    ).fetchall()
    # Claim them immediately so other workers don't double-pick
    for row in rows:
        conn.execute(
            "UPDATE task_runs SET status = 'claimed' WHERE id = ? AND status = 'pending'",
            (row["id"],),
        )
    conn.commit()
    conn.close()
    return rows


def update_task_run_status(task_run_id: str, status: str, now: str) -> None:
    conn = _connect()
    conn.execute(
        "UPDATE task_runs SET status = ?, updated_at = ? WHERE id = ?",
        (status, now, task_run_id),
    )
    conn.commit()
    conn.close()


def update_task_run_result(
    task_run_id: str,
    status: str,
    passed: bool,
    score: float,
    details: str,
    now: str,
) -> None:
    conn = _connect()
    conn.execute(
        "UPDATE task_runs "
        "SET status = ?, passed = ?, score = ?, details = ?, updated_at = ? "
        "WHERE id = ?",
        (status, int(passed), score, details, now, task_run_id),
    )
    conn.commit()
    conn.close()
