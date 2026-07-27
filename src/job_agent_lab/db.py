import sqlite3
from pathlib import Path


CREATE_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id            TEXT PRIMARY KEY,
  job_link          TEXT NOT NULL,
  sheet_row_id      TEXT,
  saved_date        TEXT,
  company           TEXT,
  position          TEXT,
  location          TEXT,
  summary           TEXT,
  requirements_json TEXT,
  cover_letter      TEXT,
  status            TEXT NOT NULL,
  error_message     TEXT,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);
"""


def init_db(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(CREATE_JOBS_TABLE)
    conn.commit()
    return conn
