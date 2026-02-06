"""CLI for the agentic harness system."""

import click


@click.group()
def cli():
    """Agentic Harness"""
    pass


@cli.command()
@click.option(
    "--agent",
    required=True,
    help="Path to the agent directory or configuration file",
)
@click.option(
    "--benchmark",
    required=True,
    help="Name of the benchmark to run (e.g., swebench)",
)
@click.option(
    "--task_ids",
    default=None,
    help="Comma-separated list of task IDs to run (runs all tasks if not provided)",
)
def run(agent: str, benchmark: str, task_ids: str | None):
    """Run an agent against a benchmark."""
    click.echo(f"Running agent: {agent}")
    click.echo(f"Benchmark: {benchmark}")

    if task_ids:
        click.echo(f"Task IDs: {task_ids}")
    else:
        click.echo("Task IDs: All tasks")

    # TODO: Implement benchmark execution logic

    click.secho("Benchmark run submitted", fg="green")


@cli.command()
@click.option(
    "--run_id",
    required=True,
    help="The unique identifier for the benchmark run",
)
def status(run_id: str):
    """Check the status of a benchmark run."""
    click.echo(f"Checking status for run: {run_id}")

    # TODO: Implement status checking logic

    click.echo("Status: Not implemented yet")


@cli.command()
@click.option(
    "--run_id",
    required=True,
    help="The unique identifier for the benchmark run",
)
@click.option(
    "--format",
    type=click.Choice(["json", "table", "summary"], case_sensitive=False),
    default="json",
    help="Output format for results (default: json)",
)
def results(run_id: str, format: str):
    """Get results from a completed run."""
    click.echo(f"Retrieving results for run: {run_id}")
    click.echo(f"Format: {format}")

    # TODO: Implement results retrieval logic

    click.echo("Results: Not implemented yet")


def main():
    """Main entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
