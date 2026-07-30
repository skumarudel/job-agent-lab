"""ADK tools for interactive cover-letter polish against the local jobs DB."""

from __future__ import annotations

import os
from pathlib import Path

from docx import Document

from job_agent_lab.analysis import parse_stored_job_analysis
from job_agent_lab.cover_letter import load_base_cover_letter as _load_base_docx
from job_agent_lab.db import NOT_APPLIED_STATUS, get_job, init_db, update_cover_letter

DEFAULT_DB_PATH = Path("data/jobs.db")
DEFAULT_RESUME_PATH = Path("assets/resume.md")
DEFAULT_COVER_LETTER_PATH = Path("assets/cover_letter.docx")


def resolve_db_path() -> Path:
    """Resolve SQLite path from ``JOB_AGENT_DB_PATH`` or the default."""
    env_path = os.environ.get("JOB_AGENT_DB_PATH", "").strip()
    return Path(env_path) if env_path else DEFAULT_DB_PATH


def resolve_resume_path() -> Path:
    """Resolve resume path from ``RESUME_PATH`` or ``assets/resume.md``."""
    env_path = os.environ.get("RESUME_PATH", "").strip()
    return Path(env_path) if env_path else DEFAULT_RESUME_PATH


def resolve_base_cover_letter_path() -> Path:
    """Resolve base cover letter path from env or assets default."""
    env_path = os.environ.get("BASE_COVER_LETTER_PATH", "").strip()
    return Path(env_path) if env_path else DEFAULT_COVER_LETTER_PATH


def list_jobs_for_apply(
    only_not_applied: bool = True,
    only_with_cover_letter: bool = False,
) -> dict:
    """List jobs from SQLite that are useful for apply-day polish.

    Args:
        only_not_applied: If True, keep rows with application_status Not applied.
        only_with_cover_letter: If True, only rows that already have a draft letter.

    Returns:
        Dict with success flag and a list of job summaries.
    """
    db_path = resolve_db_path()
    if not db_path.is_file():
        return {
            "success": False,
            "error": f"Database not found at {db_path}. Run `job-agent-lab run` first.",
            "db_path": str(db_path),
        }

    conn = init_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT job_id, company, position, application_status, status,
                   cover_letter, summary
            FROM jobs
            ORDER BY updated_at DESC
            """
        ).fetchall()
    finally:
        conn.close()

    jobs: list[dict] = []
    for row in rows:
        job_id, company, position, app_status, status, cover, summary = row
        app_status = app_status or ""
        has_letter = bool(cover and str(cover).strip())
        if only_not_applied and app_status != NOT_APPLIED_STATUS:
            continue
        if only_with_cover_letter and not has_letter:
            continue
        jobs.append(
            {
                "job_id": job_id,
                "company": company or "",
                "position": position or "",
                "application_status": app_status,
                "pipeline_status": status or "",
                "has_cover_letter": has_letter,
                "summary_preview": ((summary or "")[:240]),
            }
        )

    return {
        "success": True,
        "db_path": str(db_path),
        "count": len(jobs),
        "jobs": jobs,
    }


def get_job_for_apply(job_id: str) -> dict:
    """Load one job including analysis and current cover letter draft.

    Args:
        job_id: Primary key from the jobs table.

    Returns:
        Dict with success flag and job payload for cover-letter refinement.
    """
    job_id = (job_id or "").strip()
    if not job_id:
        return {"success": False, "error": "job_id is required"}

    db_path = resolve_db_path()
    if not db_path.is_file():
        return {
            "success": False,
            "error": f"Database not found at {db_path}. Run `job-agent-lab run` first.",
        }

    conn = init_db(db_path)
    try:
        job = get_job(conn, job_id)
    finally:
        conn.close()

    if job is None:
        return {"success": False, "error": f"job not found: {job_id}"}

    analysis = parse_stored_job_analysis(
        job.get("summary") or "",
        job.get("requirements_json") or "[]",
    )
    return {
        "success": True,
        "job": {
            "job_id": job.get("job_id"),
            "company": job.get("company") or "",
            "position": job.get("position") or "",
            "location": job.get("location") or "",
            "job_link": job.get("job_link") or "",
            "application_status": job.get("application_status") or "",
            "pipeline_status": job.get("status") or "",
            "summary": analysis.summary,
            "key_requirements": analysis.key_requirements,
            "important_skills": analysis.important_skills,
            "role_family": analysis.role_family,
            "cover_letter": job.get("cover_letter") or "",
            "error_message": job.get("error_message") or "",
        },
    }


def load_base_cover_letter_tool() -> dict:
    """Load the candidate base cover letter from the configured docx path.

    Returns:
        Dict with success flag and base letter text.
    """
    path = resolve_base_cover_letter_path()
    try:
        text = _load_base_docx(path)
    except (OSError, ValueError) as exc:
        return {"success": False, "error": str(exc), "path": str(path)}
    return {"success": True, "path": str(path), "base_cover_letter": text}


def load_resume_text() -> dict:
    """Load resume text from ``RESUME_PATH`` (md/txt/docx supported).

    Returns:
        Dict with success flag and resume plain text.
    """
    path = resolve_resume_path()
    if not path.is_file():
        return {
            "success": False,
            "error": (
                f"Resume not found at {path}. Add your resume or set RESUME_PATH."
            ),
            "path": str(path),
        }

    suffix = path.suffix.lower()
    try:
        if suffix in {".md", ".txt", ".text"}:
            text = path.read_text(encoding="utf-8").strip()
        elif suffix == ".docx":
            document = Document(path)
            paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
            text = "\n\n".join(paragraphs)
        else:
            return {
                "success": False,
                "error": f"Unsupported resume format {suffix}; use .md, .txt, or .docx",
                "path": str(path),
            }
    except OSError as exc:
        return {"success": False, "error": str(exc), "path": str(path)}

    if not text:
        return {"success": False, "error": "Resume file is empty", "path": str(path)}

    return {"success": True, "path": str(path), "resume_text": text}


def save_cover_letter_to_db(job_id: str, cover_letter: str) -> dict:
    """Save a polished cover letter into SQLite for the given job.

    Args:
        job_id: Primary key of the job to update.
        cover_letter: Final letter text to store.

    Returns:
        Dict with success flag.
    """
    job_id = (job_id or "").strip()
    letter = (cover_letter or "").strip()
    if not job_id:
        return {"success": False, "error": "job_id is required"}
    if not letter:
        return {"success": False, "error": "cover_letter is empty"}

    db_path = resolve_db_path()
    if not db_path.is_file():
        return {
            "success": False,
            "error": f"Database not found at {db_path}",
            "db_path": str(db_path),
        }

    conn = init_db(db_path)
    try:
        job = get_job(conn, job_id)
        if job is None:
            return {"success": False, "error": f"job not found: {job_id}"}
        update_cover_letter(conn, job_id, letter)
    finally:
        conn.close()

    return {
        "success": True,
        "job_id": job_id,
        "db_path": str(db_path),
        "message": "Cover letter saved to the database.",
    }
