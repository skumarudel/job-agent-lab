from __future__ import annotations

import os
from typing import Any, Sequence

from google.oauth2 import service_account
from googleapiclient.discovery import build

from job_agent_lab.db import JobRow, existing_job_ids, insert_pending_job
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
    return " ".join(str(value or "").strip().lower().split())


def _cell(row: Sequence[Any], index: int | None) -> str:
    if index is None or index >= len(row) or row[index] is None:
        return ""
    return str(row[index]).strip()


def normalize_application_status(value: str) -> str:
    key = " ".join(value.strip().lower().split())
    return _APPLICATION_STATUS_ALIASES.get(key, value.strip())


def normalize_interview_stage(value: str) -> str:
    key = " ".join(value.strip().lower().split())
    if not key:
        return ""
    return _INTERVIEW_STAGE_ALIASES.get(key, value.strip())


def map_headers(header_row: Sequence[Any]) -> dict[str, int]:
    """Map logical JobRow fields to column indexes from the sheet header row."""
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
    """Parse sheet grid values using header names. Skips empty Job links."""
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
    known = existing_job_ids(conn)
    new_jobs: list[tuple[str, JobRow]] = []
    for row in rows:
        job_id = job_id_from_link(row.job_link)
        if job_id not in known:
            new_jobs.append((job_id, row))
    return new_jobs


def sync_new_jobs_from_rows(conn, rows: Sequence[JobRow]) -> list[str]:
    """Insert new sheet jobs as pending. Returns inserted job_ids."""
    inserted: list[str] = []
    for job_id, row in new_jobs_not_in_db(conn, rows):
        if insert_pending_job(conn, job_id, row):
            inserted.append(job_id)
    return inserted
