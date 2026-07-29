"""Read job rows from Google Sheets and sync new links into SQLite."""

from __future__ import annotations

import os
from typing import Any, Sequence

from google.oauth2 import service_account
from googleapiclient.discovery import build

from job_agent_lab.db import (
    JobRow,
    existing_job_ids,
    insert_pending_job,
    update_job_sheet_metadata,
)
from job_agent_lab.job_id import job_id_from_link

SCOPES = ("https://www.googleapis.com/auth/spreadsheets.readonly",)
DEFAULT_RANGE = "A:Z"

# Logical field -> accepted header names (matched case-insensitively)
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "sheet_row_id": ("id", "sheet id", "row id"),
    "saved_date": ("date", "saved date"),
    "company": ("company",),
    "position": ("position", "title", "role"),
    "location": ("location",),
    "job_link": ("job link", "job url", "link", "url"),
    # Sheet "Status" = Applied / Not applied (not the DB pipeline status)
    "application_status": ("status", "application status", "applied status"),
    "notes": ("notes", "status notes", "note"),
    "interview_stage": ("interview stage", "interview_stage", "stage"),
}

# Soft-normalize common Status values from the sheet dropdown
_APPLICATION_STATUS_ALIASES = {
    "applied": "Applied",
    "not applied": "Not applied",
    "not-applied": "Not applied",
    "notapplied": "Not applied",
}

# Soft-normalize Interview stage dropdown values
_INTERVIEW_STAGE_ALIASES = {
    "waiting on a response": "Waiting on a response",
    "waiting on response": "Waiting on a response",
    "screen": "Screen",
    "1st interview": "1st interview",
    "first interview": "1st interview",
    "2nd interview": "2nd interview",
    "second interview": "2nd interview",
    "offer": "Offer",
    "declined": "Declined",
    "no response": "No Response",
    "no-response": "No Response",
}

INTERVIEW_STAGES = (
    "Waiting on a response",
    "Screen",
    "1st interview",
    "2nd interview",
    "Offer",
    "Declined",
    "No Response",
)


def _normalize_header(value: Any) -> str:
    """Lowercase and collapse whitespace in a header cell for matching."""
    return " ".join(str(value or "").strip().lower().split())


def _cell(row: Sequence[Any], index: int | None) -> str:
    """Return a stripped string cell, or empty string if missing."""
    if index is None or index >= len(row) or row[index] is None:
        return ""
    return str(row[index]).strip()


def normalize_application_status(value: str) -> str:
    """Normalize sheet Status to Applied / Not applied when recognized.

    Args:
        value: Raw status cell from the sheet.

    Returns:
        Canonical ``Applied`` or ``Not applied``, or the trimmed original
        string if it does not match a known alias.
    """
    key = " ".join(value.strip().lower().split())
    return _APPLICATION_STATUS_ALIASES.get(key, value.strip())


def normalize_interview_stage(value: str) -> str:
    """Normalize sheet Interview stage to a known dropdown label when possible.

    Known labels are listed in ``INTERVIEW_STAGES``.

    Args:
        value: Raw interview-stage cell from the sheet.

    Returns:
        Canonical stage string, empty string if blank, or the trimmed original
        if unrecognized.
    """
    key = " ".join(value.strip().lower().split())
    if not key:
        return ""
    return _INTERVIEW_STAGE_ALIASES.get(key, value.strip())


def map_headers(header_row: Sequence[Any]) -> dict[str, int]:
    """Map JobRow field names to column indexes using the sheet header row.

    Args:
        header_row: First row of the sheet (column titles).

    Returns:
        Dict from logical field name (e.g. ``job_link``) to zero-based index.

    Raises:
        ValueError: If no Job link column (or accepted alias) is present.
    """
    index_by_name = {
        _normalize_header(name): i for i, name in enumerate(header_row) if _normalize_header(name)
    }
    mapping: dict[str, int] = {}
    for field, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            if alias in index_by_name:
                mapping[field] = index_by_name[alias]
                break
    if "job_link" not in mapping:
        raise ValueError(
            "Sheet header must include a Job link column "
            f"(accepted names: {', '.join(HEADER_ALIASES['job_link'])})"
        )
    return mapping


def parse_sheet_values(values: Sequence[Sequence[Any]]) -> list[JobRow]:
    """Parse a Sheets values grid into JobRow objects.

    Uses the first row as headers. Skips data rows with an empty Job link.
    Does not call Google or touch the database.

    Args:
        values: Grid from the Sheets API ``values`` list (header + data rows).

    Returns:
        Parsed jobs with normalized application_status and interview_stage.
    """
    if not values:
        return []

    columns = map_headers(values[0])
    rows: list[JobRow] = []
    for raw in values[1:]:
        job_link = _cell(raw, columns.get("job_link"))
        if not job_link:
            continue
        rows.append(
            JobRow(
                sheet_row_id=_cell(raw, columns.get("sheet_row_id")),
                saved_date=_cell(raw, columns.get("saved_date")),
                company=_cell(raw, columns.get("company")),
                position=_cell(raw, columns.get("position")),
                location=_cell(raw, columns.get("location")),
                job_link=job_link,
                application_status=normalize_application_status(
                    _cell(raw, columns.get("application_status"))
                ),
                notes=_cell(raw, columns.get("notes")),
                interview_stage=normalize_interview_stage(
                    _cell(raw, columns.get("interview_stage"))
                ),
            )
        )
    return rows


def build_sheets_service(credentials_file: str):
    """Build a read-only Google Sheets API client from a service-account JSON file.

    Args:
        credentials_file: Absolute path to the service account key JSON.

    Returns:
        Google API Resource for the Sheets v4 service.
    """
    credentials = service_account.Credentials.from_service_account_file(
        credentials_file,
        scopes=SCOPES,
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def fetch_sheet_job_rows(
    spreadsheet_id: str,
    credentials_file: str,
    range_name: str = DEFAULT_RANGE,
    service: Any | None = None,
) -> list[JobRow]:
    """Fetch and parse job rows from a Google Spreadsheet.

    Performs a network call unless ``service`` is provided (tests inject a fake).

    Args:
        spreadsheet_id: Spreadsheet id from the sheet URL.
        credentials_file: Path to the service account JSON.
        range_name: A1 range to read (default ``A:Z``).
        service: Optional pre-built Sheets service (for tests).

    Returns:
        Job rows with a non-empty Job link.
    """
    sheets_service = service or build_sheets_service(credentials_file)
    result = (
        sheets_service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_name)
        .execute()
    )
    values = result.get("values", [])
    return parse_sheet_values(values)


def fetch_sheet_job_rows_from_env(
    range_name: str = DEFAULT_RANGE,
    service: Any | None = None,
) -> list[JobRow]:
    """Fetch job rows using ``GOOGLE_SERVICE_ACCOUNT_FILE`` and ``GOOGLE_SHEET_ID``.

    Args:
        range_name: A1 range to read (default ``A:Z``).
        service: Optional pre-built Sheets service (for tests).

    Returns:
        Job rows with a non-empty Job link.

    Raises:
        ValueError: If either required env var is missing or empty.
    """
    credentials_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    spreadsheet_id = os.environ.get("GOOGLE_SHEET_ID", "").strip()
    if not credentials_file:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_FILE is not set")
    if not spreadsheet_id:
        raise ValueError("GOOGLE_SHEET_ID is not set")
    return fetch_sheet_job_rows(
        spreadsheet_id=spreadsheet_id,
        credentials_file=credentials_file,
        range_name=range_name,
        service=service,
    )


def new_jobs_not_in_db(conn, rows: Sequence[JobRow]) -> list[tuple[str, JobRow]]:
    """Filter sheet rows to those whose job_id is not already in SQLite.

    Does not insert rows. Does not filter by Applied / Not applied.

    Args:
        conn: Open SQLite connection from ``init_db``.
        rows: Parsed sheet jobs.

    Returns:
        List of ``(job_id, JobRow)`` pairs that are new to the database.
    """
    known = existing_job_ids(conn)
    new_jobs: list[tuple[str, JobRow]] = []
    for row in rows:
        job_id = job_id_from_link(row.job_link)
        if job_id not in known:
            new_jobs.append((job_id, row))
    return new_jobs


def sync_new_jobs_from_rows(conn, rows: Sequence[JobRow]) -> list[str]:
    """Insert new sheet jobs and refresh metadata for existing ones.

    All statuses (Applied / Not applied) are stored. Scraping and cover
    letters still run only for ``Not applied`` jobs via the process helpers.

    Args:
        conn: Open SQLite connection from ``init_db``.
        rows: Parsed sheet jobs (typically from ``fetch_sheet_job_rows*``).

    Returns:
        job_id strings newly inserted on this call (metadata updates omitted).
    """
    known = existing_job_ids(conn)
    inserted: list[str] = []
    for row in rows:
        job_id = job_id_from_link(row.job_link)
        if job_id in known:
            update_job_sheet_metadata(conn, job_id, row)
            continue
        if insert_pending_job(conn, job_id, row):
            inserted.append(job_id)
            known.add(job_id)
    return inserted
