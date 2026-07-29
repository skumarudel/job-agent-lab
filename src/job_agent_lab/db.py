"""SQLite access for jobs: schema, sheet row model, and pending inserts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
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
