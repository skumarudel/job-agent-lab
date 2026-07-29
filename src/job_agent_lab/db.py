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
    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    for name, col_type in _EXTRA_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {col_type}")


def init_db(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(CREATE_JOBS_TABLE)
    _ensure_extra_columns(conn)
    conn.commit()
    return conn


def existing_job_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT job_id FROM jobs").fetchall()
    return {row[0] for row in rows}


def insert_pending_job(conn: sqlite3.Connection, job_id: str, row: JobRow) -> bool:
    """Insert a pending job. Returns True if inserted, False if job_id already exists."""
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
