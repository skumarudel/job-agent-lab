from __future__ import annotations

import os
from typing import Any, Sequence

from google.oauth2 import service_account
from googleapiclient.discovery import build

from job_agent_lab.db import JobRow, existing_job_ids, insert_pending_job
from job_agent_lab.job_id import job_id_from_link

SCOPES = ("https://www.googleapis.com/auth/spreadsheets.readonly",)
DEFAULT_RANGE = "A:H"

# Header: ID | Date | Company | Position | Location | Job link | Status Notes | Interview stage
COL_ID = 0
COL_DATE = 1
COL_COMPANY = 2
COL_POSITION = 3
COL_LOCATION = 4
COL_JOB_LINK = 5


def _cell(row: Sequence[Any], index: int) -> str:
    if index >= len(row) or row[index] is None:
        return ""
    return str(row[index]).strip()


def parse_sheet_values(values: Sequence[Sequence[Any]]) -> list[JobRow]:
    """Parse sheet grid values into JobRow list. Skips header and empty Job links."""
    if not values:
        return []

    rows: list[JobRow] = []
    for raw in values[1:]:
        job_link = _cell(raw, COL_JOB_LINK)
        if not job_link:
            continue
        rows.append(
            JobRow(
                sheet_row_id=_cell(raw, COL_ID),
                saved_date=_cell(raw, COL_DATE),
                company=_cell(raw, COL_COMPANY),
                position=_cell(raw, COL_POSITION),
                location=_cell(raw, COL_LOCATION),
                job_link=job_link,
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
