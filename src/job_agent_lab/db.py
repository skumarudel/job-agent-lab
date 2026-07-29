"""SQLite access for jobs: schema, sheet row model, and status updates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import sqlite3


CREATE_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id              TEXT PRIMARY KEY,
  job_link            TEXT NOT NULL,
  sheet_row_id        TEXT,
  saved_date          TEXT,
  company             TEXT,
  position            TEXT,
  location            TEXT,
  application_status  TEXT,
  notes               TEXT,
  interview_stage     TEXT,
  summary             TEXT,
  requirements_json   TEXT,
  cover_letter        TEXT,
  status              TEXT NOT NULL,
  error_message       TEXT,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);
"""

# Extra columns for DBs created before this change
_EXTRA_COLUMNS = (
    ("application_status", "TEXT"),
    ("notes", "TEXT"),
    ("interview_stage", "TEXT"),
)


@dataclass(frozen=True)
class JobRow:
    """One job row parsed from the Google Sheet (before/while storing in SQLite).

    Attributes:
        sheet_row_id: Value from the sheet ID column.
        saved_date: Value from the sheet Date column.
        company: Employer name from the sheet.
        position: Role title from the sheet.
        location: Location from the sheet.
        job_link: Posting URL (required for processing).
        application_status: Sheet Status (Applied / Not applied), not pipeline status.
        notes: Free-text notes from the sheet.
        interview_stage: Sheet Interview stage dropdown value.
    """

    sheet_row_id: str
    saved_date: str
    company: str
    position: str
    location: str
    job_link: str
    application_status: str = ""
    notes: str = ""
    interview_stage: str = ""


def _ensure_extra_columns(conn: sqlite3.Connection) -> None:
    """Add newer columns to an existing jobs table if they are missing."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    for name, col_type in _EXTRA_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {col_type}")


def init_db(path: str | Path) -> sqlite3.Connection:
    """Create or open the jobs SQLite database and ensure the schema exists.

    Creates parent directories as needed. Pipeline ``status`` values are
    ``pending`` / ``ready`` / ``error`` (separate from ``application_status``).

    Args:
        path: Filesystem path to the ``.db`` file (e.g. ``data/jobs.db``).

    Returns:
        An open ``sqlite3.Connection`` with the ``jobs`` table ready.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(CREATE_JOBS_TABLE)
    _ensure_extra_columns(conn)
    conn.commit()
    return conn


def existing_job_ids(conn: sqlite3.Connection) -> set[str]:
    """Return the set of job_id values already stored in the jobs table.

    Args:
        conn: Open connection from ``init_db``.

    Returns:
        Set of primary-key job ids currently in the database.
    """
    rows = conn.execute("SELECT job_id FROM jobs").fetchall()
    return {row[0] for row in rows}


def insert_pending_job(conn: sqlite3.Connection, job_id: str, row: JobRow) -> bool:
    """Insert a sheet job with pipeline status ``pending``.

    Leaves ``summary``, ``requirements_json``, and ``cover_letter`` null for
    later processing. Does not update an existing row.

    Args:
        conn: Open connection from ``init_db``.
        job_id: Primary key from ``job_id_from_link``.
        row: Sheet fields to store alongside the link.

    Returns:
        True if a new row was inserted; False if ``job_id`` already existed.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            """
            INSERT INTO jobs (
              job_id, job_link, sheet_row_id, saved_date, company, position,
              location, application_status, notes, interview_stage,
              summary, requirements_json, cover_letter, status,
              error_message, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 'pending', NULL, ?, ?)
            """,
            (
                job_id,
                row.job_link,
                row.sheet_row_id,
                row.saved_date,
                row.company,
                row.position,
                row.location,
                row.application_status,
                row.notes,
                row.interview_stage,
                now,
                now,
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def list_jobs_to_process(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Return jobs that still need scrape/summary processing.

    Includes pipeline ``pending`` and ``error`` rows (errors are retried).
    Does not include ``ready`` jobs.

    Args:
        conn: Open connection from ``init_db``.

    Returns:
        List of ``(job_id, job_link)`` pairs ordered by ``created_at``.
    """
    rows = conn.execute(
        """
        SELECT job_id, job_link FROM jobs
        WHERE status IN ('pending', 'error')
        ORDER BY created_at ASC
        """
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def mark_job_ready(
    conn: sqlite3.Connection,
    job_id: str,
    summary: str,
    requirements: list[str] | dict | Any,
) -> None:
    """Store summary/requirements and set pipeline status to ``ready``.

    Does not set ``cover_letter`` (left for a later step). Clears
    ``error_message``.

    Args:
        conn: Open connection from ``init_db``.
        job_id: Primary key of the job to update.
        summary: Short text summary of the posting.
        requirements: Either a list of requirement strings (legacy) or a
            structured mapping / Pydantic ``model_dump()`` payload stored as
            JSON (preferred: ``JobAnalysis`` fields).
    """
    now = datetime.now(timezone.utc).isoformat()
    if hasattr(requirements, "model_dump"):
        payload = requirements.model_dump()
    else:
        payload = requirements
    conn.execute(
        """
        UPDATE jobs
        SET summary = ?,
            requirements_json = ?,
            status = 'ready',
            error_message = NULL,
            updated_at = ?
        WHERE job_id = ?
        """,
        (summary, json.dumps(payload), now, job_id),
    )
    conn.commit()


def mark_job_error(conn: sqlite3.Connection, job_id: str, error_message: str) -> None:
    """Set pipeline status to ``error`` and record the failure message.

    Args:
        conn: Open connection from ``init_db``.
        job_id: Primary key of the job to update.
        error_message: Human-readable reason for the failure.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE jobs
        SET status = 'error',
            error_message = ?,
            updated_at = ?
        WHERE job_id = ?
        """,
        (error_message, now, job_id),
    )
    conn.commit()


def list_jobs_needing_cover_letter(
    conn: sqlite3.Connection,
) -> list[tuple[str, str, str, str, str]]:
    """Return ready jobs that do not yet have a cover letter.

    Args:
        conn: Open connection from ``init_db``.

    Returns:
        List of ``(job_id, company, position, summary, requirements_json)``
        ordered by ``created_at``. ``summary`` / ``requirements_json`` may be
        empty strings if missing.
    """
    rows = conn.execute(
        """
        SELECT job_id, company, position, summary, requirements_json
        FROM jobs
        WHERE status = 'ready'
          AND (cover_letter IS NULL OR TRIM(cover_letter) = '')
        ORDER BY created_at ASC
        """
    ).fetchall()
    return [
        (
            row[0],
            row[1] or "",
            row[2] or "",
            row[3] or "",
            row[4] or "[]",
        )
        for row in rows
    ]


def update_cover_letter(conn: sqlite3.Connection, job_id: str, cover_letter: str) -> None:
    """Store a cover letter for a job without changing pipeline status.

    Args:
        conn: Open connection from ``init_db``.
        job_id: Primary key of the job to update.
        cover_letter: Full tailored letter text to store.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE jobs
        SET cover_letter = ?,
            updated_at = ?
        WHERE job_id = ?
        """,
        (cover_letter, now, job_id),
    )
    conn.commit()
