"""Core task execution logic: setup → agent → evaluate."""

import json
import os
import pty
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import db

# Resolved at import time so subprocesses inherit the right paths
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BENCHMARKS_DIR = _REPO_ROOT / "benchmarks"
WORKSPACES_DIR = _REPO_ROOT / "workspaces"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Config loaders ───────────────────────────────────────────────────────────


def load_benchmark_config(benchmark_name: str) -> dict:
    """Load a benchmark's harness.json and dataset.json into a single dict."""
    benchmark_dir = BENCHMARKS_DIR / benchmark_name
    harness_path = benchmark_dir / "harness.json"

    if not harness_path.exists():
        raise FileNotFoundError(
            f"No harness.json found for benchmark '{benchmark_name}' "
            f"(looked in {benchmark_dir})"
        )

    with open(harness_path) as f:
        config = json.load(f)

    config["benchmark_dir"] = str(benchmark_dir)

    dataset_path = benchmark_dir / "dataset.json"
    with open(dataset_path) as f:
        raw_tasks = json.load(f)

    # Normalize tasks to always have "task_id" and "description" keys
    id_field = config["task_id_field"]
    desc_field = config["description_field"]
    config["tasks"] = [
        {
            "task_id": t[id_field],
            "description": t[desc_field],
            **t,
        }
        for t in raw_tasks
    ]

    return config


def load_agent_config(agent_path: str) -> dict:
    """Load an agent's harness config.

    Looks for harness.json in two places (in order):
    1. agents/configs/<agent-name>.json  — for submodule agents where we can't
       add files inside the submodule directory
    2. <agent_dir>/harness.json          — for self-contained agent directories
    """
    agent_dir = Path(agent_path)
    if not agent_dir.is_absolute():
        agent_dir = _REPO_ROOT / agent_dir

    # Preferred location: agents/configs/<name>.json
    sidecar = _REPO_ROOT / "agents" / "configs" / f"{agent_dir.name}.json"
    if sidecar.exists():
        with open(sidecar) as f:
            return json.load(f)

    # Fallback: harness.json inside the agent directory itself
    harness_path = agent_dir / "harness.json"
    if harness_path.exists():
        with open(harness_path) as f:
            return json.load(f)

    raise FileNotFoundError(
        f"No harness config found for agent '{agent_path}'. "
        f"Looked in '{sidecar}' and '{harness_path}'."
    )


# ── Run submission ────────────────────────────────────────────────────────────


def submit_run(agent: str, benchmark: str, task_ids: list[str] | None) -> str:
    """Insert a run + task_runs into the DB queue and return the run_id."""
    config = load_benchmark_config(benchmark)
    tasks = config["tasks"]

    if task_ids:
        tasks = [t for t in tasks if t["task_id"] in task_ids]
        if not tasks:
            raise ValueError(
                f"None of the requested task_ids {task_ids} were found in {benchmark}"
            )

    run_id = uuid.uuid4().hex[:8]
    now = _now()
    db.create_run(run_id, agent, benchmark, now)

    for task in tasks:
        task_run_id = uuid.uuid4().hex[:8]
        db.create_task_run(task_run_id, run_id, task["task_id"], now)

    return run_id


# ── Task execution ────────────────────────────────────────────────────────────


def _fmt(template_parts: list[str], **kwargs) -> list[str]:
    """Fill {placeholders} in each element of a command template."""
    return [part.format(**kwargs) for part in template_parts]


# ── Docker helpers ────────────────────────────────────────────────────────────

# Only these env vars are forwarded into containers to avoid leaking host secrets.
_ENV_PASSTHROUGH = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"}


def _translate_cmd_for_container(
    cmd: list[str],
    host_benchmark_dir: str,
    container_benchmark_dir: str,
    host_workspace: str,
    container_workspace: str,
) -> list[str]:
    """Replace host-side absolute path prefixes with container-side mount points."""
    return [
        part.replace(host_benchmark_dir, container_benchmark_dir)
            .replace(host_workspace, container_workspace)
        for part in cmd
    ]


def _build_docker_cmd(
    cmd: list[str],
    *,
    image: str,
    host_benchmark_dir: str,
    container_benchmark_dir: str,
    host_workspace: str,
    container_workspace: str,
    env: dict,
    extra_flags: list[str] | None = None,
) -> list[str]:
    """Wrap `cmd` in a `docker run --rm` invocation with bind-mounts.

    The benchmark directory is mounted read-only (the container cannot tamper with
    benchmark scripts). The workspace is mounted read-write so the container can
    create and read task files.

    Only allowlisted env vars are forwarded into the container.
    """
    docker_cmd = [
        "docker", "run", "--rm",
        *(extra_flags or []),
        "-v", f"{host_benchmark_dir}:{container_benchmark_dir}:ro",
        "-v", f"{host_workspace}:{container_workspace}:rw",
        "-w", container_workspace,
    ]
    for key in _ENV_PASSTHROUGH:
        if key in env:
            docker_cmd += ["-e", f"{key}={env[key]}"]
    docker_cmd.append(image)
    docker_cmd.extend(cmd)
    return docker_cmd


def execute_task_run(
    task_run_id: str,
    run_id: str,
    task_id: str,
    agent: str,
    benchmark: str,
) -> None:
    """Execute setup → agent → evaluate for a single task, persisting results."""
    now = _now()
    db.update_task_run_status(task_run_id, "running", now)
    db.refresh_run_status(run_id, now)

    try:
        benchmark_config = load_benchmark_config(benchmark)
        agent_config = load_agent_config(agent)

        task = next(t for t in benchmark_config["tasks"] if t["task_id"] == task_id)
        benchmark_dir = benchmark_config["benchmark_dir"]

        workspace = WORKSPACES_DIR / run_id / task_id
        workspace.mkdir(parents=True, exist_ok=True)

        # Load env vars from .env so subprocesses (e.g. mini) pick up API keys
        env = _build_env()

        # Resolve Docker config for this benchmark (optional — falls back to host)
        docker_cfg = benchmark_config.get("docker")
        if docker_cfg and not shutil.which("docker"):
            raise RuntimeError(
                "This benchmark requires Docker but 'docker' was not found on PATH. "
                "Install Docker Desktop or Docker Engine and ensure the daemon is running."
            )
        container_benchmark_dir = docker_cfg["benchmark_dir_in_container"] if docker_cfg else None
        container_workspace = docker_cfg["workspace_dir_in_container"] if docker_cfg else None

        # 1. Setup
        setup_cmd = _fmt(
            benchmark_config["setup_cmd"],
            benchmark_dir=benchmark_dir,
            workspace=str(workspace),
            task_id=task_id,
        )
        if docker_cfg:
            setup_cmd = _build_docker_cmd(
                _translate_cmd_for_container(
                    setup_cmd, benchmark_dir, container_benchmark_dir,
                    str(workspace), container_workspace,
                ),
                image=docker_cfg["image"],
                host_benchmark_dir=benchmark_dir,
                container_benchmark_dir=container_benchmark_dir,
                host_workspace=str(workspace),
                container_workspace=container_workspace,
                env=env,
            )
        subprocess.run(setup_cmd, check=True, capture_output=True, env=env)

        # 2. Run agent inside the workspace via a pseudo-terminal so that
        #    TUI-based agents have access to a real terminal device.
        #    If the agent config includes a "docker" block, wrap the command in
        #    `docker run -t` so the container also gets a PTY.
        agent_cmd = _fmt(
            agent_config["run_cmd"],
            task_description=task["description"],
        )
        agent_docker_cfg = agent_config.get("docker")
        if agent_docker_cfg:
            agent_cmd = _build_docker_cmd(
                agent_cmd,
                image=agent_docker_cfg["image"],
                host_benchmark_dir=benchmark_dir,
                container_benchmark_dir=agent_docker_cfg.get(
                    "benchmark_dir_in_container", "/benchmark"
                ),
                host_workspace=str(workspace),
                container_workspace=agent_docker_cfg.get(
                    "workspace_dir_in_container", "/workspace"
                ),
                env=env,
                extra_flags=["-t"],  # allocate PTY inside the container
            )
        _run_in_pty(agent_cmd, cwd=str(workspace), env=env, timeout=300)

        # 3. Evaluate
        eval_cmd = _fmt(
            benchmark_config["evaluate_cmd"],
            benchmark_dir=benchmark_dir,
            workspace=str(workspace),
            task_id=task_id,
        )
        if docker_cfg:
            eval_cmd = _build_docker_cmd(
                _translate_cmd_for_container(
                    eval_cmd, benchmark_dir, container_benchmark_dir,
                    str(workspace), container_workspace,
                ),
                image=docker_cfg["image"],
                host_benchmark_dir=benchmark_dir,
                container_benchmark_dir=container_benchmark_dir,
                host_workspace=str(workspace),
                container_workspace=container_workspace,
                env=env,
            )
        eval_proc = subprocess.run(
            eval_cmd, capture_output=True, text=True, env=env
        )

        result_type = benchmark_config.get("evaluate_result", "json_stdout")
        if result_type == "exit_code":
            # Pass/fail determined purely by exit code; no JSON expected
            passed = eval_proc.returncode == 0
            score = 1.0 if passed else 0.0
            details = json.dumps({"exit_code": eval_proc.returncode, "stdout": eval_proc.stdout})
        else:
            # Default: "json_stdout" — evaluate script prints a JSON result object
            eval_output = json.loads(eval_proc.stdout)
            passed = bool(eval_output.get("passed", False))
            score = float(eval_output.get("score", 1.0 if passed else 0.0))
            details = json.dumps(eval_output.get("details", {}))

    except subprocess.TimeoutExpired as exc:
        # Agent hung past the timeout — clean up the workspace so stale files
        # don't accumulate and don't interfere with future runs.
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
        now = _now()
        db.update_task_run_result(
            task_run_id,
            "failed",
            False,
            0.0,
            json.dumps({"error": f"Agent timed out after {exc.timeout}s"}),
            now,
        )
        db.refresh_run_status(run_id, now)
        return

    except Exception as exc:
        now = _now()
        db.update_task_run_result(
            task_run_id,
            "failed",
            False,
            0.0,
            json.dumps({"error": str(exc)}),
            now,
        )
        db.refresh_run_status(run_id, now)
        return

    now = _now()
    db.update_task_run_result(task_run_id, "completed", passed, score, details, now)
    db.refresh_run_status(run_id, now)


_SIGTERM_GRACE = 5  # seconds to wait after SIGTERM before escalating to SIGKILL


def _run_in_pty(cmd: list[str], *, cwd: str, env: dict, timeout: int) -> None:
    """Run a command inside a pseudo-terminal.

    Raises subprocess.TimeoutExpired if the process does not finish within
    `timeout` seconds. On timeout, sends SIGTERM and waits up to _SIGTERM_GRACE
    seconds for a clean exit before escalating to SIGKILL — guaranteeing the
    worker thread is never blocked indefinitely.
    """
    import select
    import signal
    import time

    pid, master_fd = pty.fork()
    if pid == 0:
        # Child process: exec the command
        if cwd:
            os.chdir(cwd)
        os.execvpe(cmd[0], cmd, env)
        os._exit(1)  # unreachable, but safety net

    # Parent: drain the pty output and enforce timeout
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                os.kill(pid, signal.SIGTERM)
                timed_out = True
                break
            ready, _, _ = select.select([master_fd], [], [], min(remaining, 1.0))
            if ready:
                try:
                    os.read(master_fd, 4096)  # drain output; we don't need it
                except OSError:
                    break  # child closed the pty
            # Check if child exited
            if os.waitpid(pid, os.WNOHANG)[0] != 0:
                break
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass

        if timed_out:
            # Give the process a short grace period to exit on SIGTERM, then
            # escalate to SIGKILL so this thread never blocks indefinitely.
            grace_deadline = time.monotonic() + _SIGTERM_GRACE
            while time.monotonic() < grace_deadline:
                if os.waitpid(pid, os.WNOHANG)[0] != 0:
                    break  # process exited cleanly within grace period
                time.sleep(0.1)
            else:
                # Grace period exhausted — force kill
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # already gone
            try:
                os.waitpid(pid, 0)  # safe: process is guaranteed dead or dying
            except ChildProcessError:
                pass
        else:
            # Normal exit: process already confirmed gone via WNOHANG above
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass

    if timed_out:
        raise subprocess.TimeoutExpired(cmd, timeout)


def _build_env() -> dict:
    """Return os.environ merged with any variables from the repo's .env file."""
    env = os.environ.copy()
    env_file = _REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env.setdefault(key.strip(), value.strip())
    return env
