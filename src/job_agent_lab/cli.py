"""CLI orchestration: ``run``, ``list``, and ``show``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv

from job_agent_lab.analysis import parse_stored_job_analysis
from job_agent_lab.cover_letter import process_cover_letters
from job_agent_lab.db import get_job, init_db, list_ready_jobs
from job_agent_lab.scrape import process_pending_jobs
from job_agent_lab.sheets import fetch_sheet_job_rows_from_env, sync_new_jobs_from_rows

DEFAULT_DB_PATH = Path("data/jobs.db")


def resolve_db_path(cli_db: str | None = None) -> Path:
    """Resolve the SQLite path from ``--db``, env, or the default.

    Args:
        cli_db: Optional path from the ``--db`` flag.

    Returns:
        Path to the jobs database file.
    """
    if cli_db:
        return Path(cli_db)
    env_path = os.environ.get("JOB_AGENT_DB_PATH", "").strip()
    if env_path:
        return Path(env_path)
    return DEFAULT_DB_PATH


def run_pipeline(db_path: str | Path) -> dict[str, Any]:
    """Run sheet sync → scrape/analyze → cover letters once.

    Args:
        db_path: Filesystem path for ``init_db``.

    Returns:
        Summary dict with ``db_path``, ``synced``, ``analyzed``, and ``letters``.
    """
    conn = init_db(db_path)
    try:
        rows = fetch_sheet_job_rows_from_env()
        synced_ids = sync_new_jobs_from_rows(conn, rows)
        analyzed = process_pending_jobs(conn)
        letters = process_cover_letters(conn)
        return {
            "db_path": str(db_path),
            "synced": synced_ids,
            "analyzed": analyzed,
            "letters": letters,
        }
    finally:
        conn.close()


def format_run_summary(result: dict[str, Any]) -> str:
    """Format ``run_pipeline`` result for stdout."""
    synced = result.get("synced") or []
    analyzed = result.get("analyzed") or {}
    letters = result.get("letters") or {}
    lines = [
        f"DB: {result.get('db_path')}",
        f"Synced new jobs: {len(synced)}",
        f"Analyzed ready: {len(analyzed.get('ready') or [])}",
        f"Analyzed error: {len(analyzed.get('error') or [])}",
        f"Cover letters updated: {len(letters.get('updated') or [])}",
        f"Cover letters error: {len(letters.get('error') or [])}",
    ]
    if synced:
        lines.append("New job_ids: " + ", ".join(synced))
    return "\n".join(lines)


def format_job_list(rows: Sequence[tuple[str, str, str, bool]]) -> str:
    """Format ready-job rows for ``list``."""
    if not rows:
        return "No ready jobs."
    lines = ["job_id\tcompany\tposition\thas_cover_letter"]
    for job_id, company, position, has_letter in rows:
        lines.append(
            f"{job_id}\t{company}\t{position}\t{'yes' if has_letter else 'no'}"
        )
    return "\n".join(lines)


def format_job_show(job: dict[str, Any]) -> str:
    """Format one job for ``show``."""
    analysis = parse_stored_job_analysis(
        job.get("summary") or "",
        job.get("requirements_json") or "[]",
    )
    lines = [
        f"job_id: {job.get('job_id')}",
        f"company: {job.get('company') or ''}",
        f"position: {job.get('position') or ''}",
        f"status: {job.get('status') or ''}",
        f"job_link: {job.get('job_link') or ''}",
        f"role_family: {analysis.role_family}",
        "",
        "Summary:",
        analysis.summary,
        "",
        "Key requirements:",
    ]
    for item in analysis.key_requirements:
        lines.append(f"- {item}")
    lines.extend(["", "Important skills:"])
    for item in analysis.important_skills:
        lines.append(f"- {item}")
    lines.extend(["", "Cover letter:"])
    cover = (job.get("cover_letter") or "").strip()
    lines.append(cover if cover else "(not generated yet)")
    if job.get("error_message"):
        lines.extend(["", f"error_message: {job['error_message']}"])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argparse parser for the job-agent-lab CLI."""
    parser = argparse.ArgumentParser(
        prog="job-agent-lab",
        description="Job analysis agent: sync sheet, analyze jobs, write cover letters.",
    )
    parser.add_argument(
        "--db",
        dest="db_path",
        default=None,
        help="SQLite path (default: JOB_AGENT_DB_PATH or data/jobs.db)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="Sync sheet, analyze pending jobs, write cover letters")
    sub.add_parser("list", help="List ready jobs")

    show = sub.add_parser("show", help="Show analysis and cover letter for one job")
    show.add_argument("job_id", help="Job primary key (hash of the job link)")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code (0 success, 1 error, 2 usage/not found).
    """
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    db_path = resolve_db_path(args.db_path)

    if args.command == "run":
        try:
            result = run_pipeline(db_path)
        except Exception as exc:  # noqa: BLE001 — surface run failures to the user
            print(f"run failed: {exc}", file=sys.stderr)
            return 1
        print(format_run_summary(result))
        return 0

    conn = init_db(db_path)
    try:
        if args.command == "list":
            print(format_job_list(list_ready_jobs(conn)))
            return 0

        if args.command == "show":
            job = get_job(conn, args.job_id)
            if job is None:
                print(f"job not found: {args.job_id}", file=sys.stderr)
                return 2
            print(format_job_show(job))
            return 0
    finally:
        conn.close()

    parser.error(f"unknown command: {args.command}")
    return 2
