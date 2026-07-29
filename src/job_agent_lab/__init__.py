"""Package entrypoint for ``job-agent-lab`` console script."""

from job_agent_lab.cli import main as cli_main


def main() -> None:
    """Delegate to the argparse CLI (``run`` / ``list`` / ``show``)."""
    raise SystemExit(cli_main())
