# Agentic Harness

A unified CLI for running any AI agent against any agentic benchmark. Abstracts over the differing interfaces, environments, and evaluation logic each benchmark uses — so you can add new agents and benchmarks without touching core harness code.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI  (harness)                       │
│          run  ·  status  ·  results                         │
└────────────────────────┬────────────────────────────────────┘
                         │ submit tasks
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    SQLite Queue / DB                        │
│           runs table  ·  task_runs table                    │
└────────────────────────┬────────────────────────────────────┘
                         │ poll
                         ▼
┌─────────────────────────────────────────────────────────────┐
│               Background Worker Process                     │
│         ThreadPoolExecutor  (max 3 concurrent)              │
│                                                             │
│  for each task:                                             │
│  ┌────────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │  Setup         │  │  Run Agent   │  │  Evaluate      │  │
│  │ (docker run    │→ │ (pty so TUI  │→ │ (docker run    │  │
│  │  --rm, ro      │  │  agents work)│  │  --rm, parse   │  │
│  │  bind-mount)   │  │              │  │  JSON stdout)  │  │
│  └────────────────┘  └──────────────┘  └────────────────┘  │
│         │                   │                  │            │
│         └───────────────────┴──────────────────┘            │
│                    shared workspace volume                   │
│               (workspaces/{run_id}/{task_id}/)               │
└─────────────────────────────────────────────────────────────┘
```

### Key abstraction: `harness.json`

Each benchmark and agent defines a `harness.json` that tells the harness how to invoke it using template strings. This means zero benchmark-specific code lives in the core harness.

**Benchmark `harness.json`:**
```json
{
  "task_id_field": "task_id",
  "description_field": "problem_statement",
  "setup_cmd": ["python", "{benchmark_dir}/setup.py", "--workspace-dir", "{workspace}", "--task", "{task_id}"],
  "evaluate_cmd": ["python", "{benchmark_dir}/evaluate.py", "--workspace-dir", "{workspace}", "--task", "{task_id}"],
  "evaluate_result": "json_stdout",
  "docker": {
    "image": "harness-benchmark:latest",
    "benchmark_dir_in_container": "/benchmark",
    "workspace_dir_in_container": "/workspace"
  }
}
```

The optional `"docker"` block tells the harness to run setup and evaluate inside a container. The benchmark scripts are bind-mounted read-only at `/benchmark`; the workspace is bind-mounted read-write at `/workspace`. If the block is absent the harness falls back to running those phases directly on the host.

**Agent `harness.json`:**
```json
{
  "run_cmd": ["mini", "--exit-immediately", "-y", "-t", "{task_description}"]
}
```

---

## Setup

### Prerequisites
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- [Docker](https://docs.docker.com/get-docker/) (Desktop or Engine)

### Install

```bash
# 1. Clone and enter the repo
git clone <repo-url> && cd <repo>

# 2. Initialize mini-swe-agent submodule
make init

# 3. Install dependencies
make install

# 4. Build the benchmark Docker image (used to isolate setup and evaluate phases)
docker build -f docker/benchmark.Dockerfile -t harness-benchmark:latest .
```

### Configure

Copy `.env.example` to `.env` and add your Anthropic API key:

```bash
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

Configure mini-swe-agent (run this once from your terminal):

```bash
mini-extra config setup
```

---

## Usage

### Run a benchmark

```bash
# Run all tasks in a benchmark
uv run harness run --agent agents/mini-swe-agent --benchmark bash-operations

# Run specific tasks
uv run harness run --agent agents/mini-swe-agent --benchmark bash-operations --task_ids bash-001,bash-002

# Run python-tasks benchmark
uv run harness run --agent agents/mini-swe-agent --benchmark python-tasks
```

`harness run` returns a `run_id` immediately and starts a background worker — you can submit multiple runs without blocking.

### Check status

```bash
uv run harness status --run_id <run_id>
```

Example output:
```
Run ID:    0039d516
Agent:     agents/mini-swe-agent
Benchmark: bash-operations
Status:    running

Tasks (5):
  bash-001             completed
  bash-002             running
  bash-003             pending
  bash-004             pending
  bash-005             pending
```

### Get results

```bash
uv run harness results --run_id <run_id>
```

Example output:
```json
{
  "run_id": "0039d516",
  "agent": "agents/mini-swe-agent",
  "benchmark": "bash-operations",
  "status": "completed",
  "summary": {
    "total": 5,
    "completed": 5,
    "passed": 5,
    "accuracy": 1.0
  },
  "tasks": [...]
}
```

---

## Project Structure

```
.
├── src/agentic_harness/
│   ├── cli.py          # Click CLI — run / status / results commands
│   ├── db.py           # SQLite layer (runs + task_runs tables)
│   ├── runner.py       # Core execution: setup → agent → evaluate
│   └── worker.py       # Background worker with ThreadPoolExecutor
│
├── benchmarks/
│   ├── bash-operations/
│   │   ├── harness.json    # Harness config
│   │   ├── dataset.json    # 5 bash tasks
│   │   ├── setup.py        # Workspace setup
│   │   └── evaluate.py     # Task evaluation
│   └── python-tasks/
│       ├── harness.json    # Harness config
│       ├── dataset.json    # 3 Python tasks
│       ├── prepare.py      # Workspace setup
│       └── grade.py        # Task evaluation (pytest)
│
├── agents/
│   ├── mini-swe-agent/     # git submodule
│   └── configs/
│       └── mini-swe-agent.json  # Agent invocation config
│
├── docker/
│   └── benchmark.Dockerfile    # Shared image for setup/evaluate phases
│
├── .env.example        # Environment variable template
├── pyproject.toml
└── Makefile
```

---

## How It Works

### Part 1: Core Execution

**`harness run`** calls `runner.submit_run()` which:
1. Loads `benchmarks/<name>/harness.json` and `dataset.json`
2. Filters tasks to the requested `task_ids` (or all tasks)
3. Inserts one `runs` row and one `task_runs` row per task into SQLite
4. Spawns the background worker (if not already running) via a PID-file-guarded detached subprocess

**The worker** polls the DB every 2 seconds and for each pending task:
1. **Setup** — runs the benchmark's `setup_cmd` inside a Docker container (`docker run --rm`) with the workspace bind-mounted, creating the task environment
2. **Agent** — runs the agent's `run_cmd` on the host inside the workspace directory, using a pseudo-terminal (pty) so TUI-based agents like mini-swe-agent work correctly; the workspace is shared with the containers via the bind-mount
3. **Evaluate** — runs the benchmark's `evaluate_cmd` inside Docker, parses the JSON stdout, stores `passed`/`score`/`details` in the DB

### Part 2: Queue-Based Execution

- `harness run` is non-blocking — returns a `run_id` instantly
- The worker runs as a detached subprocess (survives the CLI process exiting), tracked by a PID file at `~/.agentic_harness/worker.pid`
- A `ThreadPoolExecutor` with configurable concurrency (`HARNESS_MAX_WORKERS`, default 3) runs tasks in parallel
- All state lives in SQLite at `~/.agentic_harness/harness.db`

### Adding a New Benchmark

1. Create `benchmarks/<name>/dataset.json` with your tasks
2. Write `setup.py` and `evaluate.py` (evaluate must print JSON with `passed`, `score`, `details`)
3. Add `benchmarks/<name>/harness.json` describing how to invoke them — include a `"docker"` block with `"image"` to run setup/evaluate in a container, or omit it to run on the host

### Adding a New Agent

1. Create `agents/configs/<name>.json` with the `run_cmd` template
2. Pass `--agent agents/<name>` when running (the harness resolves the config from `agents/configs/` automatically)

---

## Benchmark Results

Tested with `mini-swe-agent` + `claude-haiku-4-5`:

| Benchmark | Tasks | Passed | Accuracy |
|-----------|-------|--------|----------|
| bash-operations | 5 | 5 | 100% |
| python-tasks | 3 | 3 | 100% |
