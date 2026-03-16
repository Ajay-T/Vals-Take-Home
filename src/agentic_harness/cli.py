"""CLI entry point for the agentic harness."""

import json
import sys

import click

from . import db, runner, worker


@click.group()
def main():
    """Agentic Harness — run any agent against any benchmark."""
    db.init_db()


# ── run ──────────────────────────────────────────────────────────────────────


@main.command()
@click.option("--agent", required=True, help="Path to agent directory (must contain harness.json)")
@click.option("--benchmark", required=True, help="Benchmark name (subdirectory of benchmarks/)")
@click.option("--task_ids", default=None, help="Comma-separated task IDs to run (default: all)")
def run(agent: str, benchmark: str, task_ids: str | None):
    """Submit an agent+benchmark run and return a run_id immediately."""
    task_id_list = [t.strip() for t in task_ids.split(",")] if task_ids else None

    try:
        run_id = runner.submit_run(agent, benchmark, task_id_list)
    except (FileNotFoundError, ValueError) as exc:
        click.secho(f"Error: {exc}", fg="red", err=True)
        sys.exit(1)

    # Start the background worker if it isn't already running
    worker.ensure_worker_running()

    click.secho(f"Run submitted:  {run_id}", fg="green")
    click.echo(f"Check status:   harness status --run_id {run_id}")
    click.echo(f"Get results:    harness results --run_id {run_id}")


# ── status ───────────────────────────────────────────────────────────────────


@main.command()
@click.option("--run_id", required=True, help="Run ID returned by `harness run`")
def status(run_id: str):
    """Check the status of a benchmark run."""
    run = db.get_run(run_id)
    if run is None:
        click.secho(f"Run '{run_id}' not found.", fg="red", err=True)
        sys.exit(1)

    task_runs = db.get_task_runs(run_id)

    click.echo(f"Run ID:    {run_id}")
    click.echo(f"Agent:     {run['agent']}")
    click.echo(f"Benchmark: {run['benchmark']}")
    click.secho(f"Status:    {run['status']}", fg=_status_color(run["status"]))
    click.echo(f"\nTasks ({len(task_runs)}):")
    for tr in task_runs:
        color = _status_color(tr["status"])
        click.echo(f"  {tr['task_id']:20s} ", nl=False)
        click.secho(tr["status"], fg=color)


# ── results ──────────────────────────────────────────────────────────────────


@main.command()
@click.option("--run_id", required=True, help="Run ID returned by `harness run`")
def results(run_id: str):
    """Print aggregated results from a completed run as JSON."""
    run = db.get_run(run_id)
    if run is None:
        click.secho(f"Run '{run_id}' not found.", fg="red", err=True)
        sys.exit(1)

    task_runs = db.get_task_runs(run_id)

    tasks_output = []
    for tr in task_runs:
        tasks_output.append({
            "task_id": tr["task_id"],
            "status": tr["status"],
            "passed": bool(tr["passed"]) if tr["passed"] is not None else None,
            "score": tr["score"],
            "details": json.loads(tr["details"]) if tr["details"] else None,
        })

    completed = [t for t in tasks_output if t["status"] == "completed"]
    passed_count = sum(1 for t in completed if t["passed"])
    total = len(tasks_output)
    accuracy = passed_count / len(completed) if completed else 0.0

    output = {
        "run_id": run_id,
        "agent": run["agent"],
        "benchmark": run["benchmark"],
        "status": run["status"],
        "summary": {
            "total": total,
            "completed": len(completed),
            "passed": passed_count,
            "accuracy": round(accuracy, 4),
        },
        "tasks": tasks_output,
    }
    click.echo(json.dumps(output, indent=2))


# ── worker (internal) ─────────────────────────────────────────────────────────


@main.command(hidden=True)
def start_worker():
    """Start the background worker loop (called internally by `run`)."""
    worker.run_worker()


# ── helpers ───────────────────────────────────────────────────────────────────


def _status_color(status: str) -> str:
    return {
        "pending": "yellow",
        "claimed": "yellow",
        "running": "cyan",
        "completed": "green",
        "failed": "red",
    }.get(status, "white")
