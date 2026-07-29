from job_agent_lab.db import JobRow, init_db, insert_pending_job
from job_agent_lab.job_id import job_id_from_link
from job_agent_lab.sheets import (
    fetch_sheet_job_rows_from_env,
    new_jobs_not_in_db,
    parse_sheet_values,
    sync_new_jobs_from_rows,
)


HEADER = [
    "ID",
    "Date",
    "Company",
    "Position",
    "Location",
    "Job link",
    "Status",
    "Notes",
    "Interview stage",
]


def test_parse_skips_empty_job_links():
    values = [
        HEADER,
        ["1", "2026-01-01", "Acme", "Eng", "Remote", "https://example.com/jobs/1", "Applied", "", ""],
        ["2", "2026-01-02", "Beta", "PM", "NYC", "", "Not applied", "notes", ""],
        ["3", "2026-01-03", "Gamma", "QA", "SF", "https://example.com/jobs/3", "Not applied", "x", "screen"],
    ]
    rows = parse_sheet_values(values)
    assert len(rows) == 2
    assert rows[0].company == "Acme"
    assert rows[0].application_status == "Applied"
    assert rows[1].job_link == "https://example.com/jobs/3"
    assert rows[1].interview_stage == "Screen"


def test_parse_sheet_values_maps_columns():
    values = [
        HEADER,
        [
            "42",
            "2026-07-01",
            "Acme",
            "SWE",
            "Remote",
            "https://Example.COM/jobs/42/",
            "not applied",
            "follow up",
            "1st interview",
        ],
    ]
    rows = parse_sheet_values(values)
    assert rows == [
        JobRow(
            sheet_row_id="42",
            saved_date="2026-07-01",
            company="Acme",
            position="SWE",
            location="Remote",
            job_link="https://Example.COM/jobs/42/",
            application_status="Not applied",
            notes="follow up",
            interview_stage="1st interview",
        )
    ]


def test_parse_infers_columns_from_reordered_headers():
    values = [
        ["Job link", "Company", "ID", "Status", "Notes", "Interview stage", "Extra"],
        ["https://example.com/jobs/7", "Acme", "7", "Applied", "hi", "waiting on a response", "ignore-me"],
        ["", "Skip", "8", "Not applied", "", "", "no-link"],
    ]
    rows = parse_sheet_values(values)
    assert len(rows) == 1
    assert rows[0].job_link == "https://example.com/jobs/7"
    assert rows[0].company == "Acme"
    assert rows[0].sheet_row_id == "7"
    assert rows[0].application_status == "Applied"
    assert rows[0].notes == "hi"
    assert rows[0].interview_stage == "Waiting on a response"


def test_normalize_interview_stage_known_values():
    from job_agent_lab.sheets import INTERVIEW_STAGES, normalize_interview_stage

    assert normalize_interview_stage("SCREEN") == "Screen"
    assert normalize_interview_stage("No Response") == "No Response"
    assert normalize_interview_stage("declined") == "Declined"
    assert normalize_interview_stage("2nd interview") == "2nd interview"
    assert normalize_interview_stage("offer") == "Offer"
    assert set(INTERVIEW_STAGES) == {
        "Waiting on a response",
        "Screen",
        "1st interview",
        "2nd interview",
        "Offer",
        "Declined",
        "No Response",
    }


def test_new_jobs_not_in_db(tmp_path):
    conn = init_db(tmp_path / "jobs.db")
    existing = JobRow(
        sheet_row_id="1",
        saved_date="2026-01-01",
        company="Acme",
        position="Eng",
        location="Remote",
        job_link="https://example.com/jobs/1",
    )
    insert_pending_job(conn, job_id_from_link(existing.job_link), existing)

    rows = [
        existing,
        JobRow(
            sheet_row_id="2",
            saved_date="2026-01-02",
            company="Beta",
            position="PM",
            location="NYC",
            job_link="https://example.com/jobs/2",
        ),
    ]
    new_jobs = new_jobs_not_in_db(conn, rows)
    assert len(new_jobs) == 1
    assert new_jobs[0][1].company == "Beta"
    conn.close()


def test_sync_inserts_pending_and_skips_duplicates(tmp_path):
    conn = init_db(tmp_path / "jobs.db")
    row = JobRow(
        sheet_row_id="9",
        saved_date="2026-01-09",
        company="Acme",
        position="Eng",
        location="Remote",
        job_link="https://example.com/jobs/9",
    )
    first = sync_new_jobs_from_rows(conn, [row])
    second = sync_new_jobs_from_rows(conn, [row])
    assert len(first) == 1
    assert second == []

    status = conn.execute(
        "SELECT status, company FROM jobs WHERE job_id = ?",
        (first[0],),
    ).fetchone()
    assert status == ("pending", "Acme")
    conn.close()


def test_fetch_from_env_uses_mocked_service(monkeypatch):
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "/tmp/fake.json")
    monkeypatch.setenv("GOOGLE_SHEET_ID", "sheet-123")

    class FakeValues:
        def get(self, spreadsheetId, range):
            assert spreadsheetId == "sheet-123"
            return self

        def execute(self):
            return {
                "values": [
                    HEADER,
                    [
                        "1",
                        "2026-01-01",
                        "Acme",
                        "Eng",
                        "Remote",
                        "https://example.com/jobs/1",
                        "",
                        "",
                    ],
                ]
            }

    class FakeSpreadsheets:
        def values(self):
            return FakeValues()

    class FakeService:
        def spreadsheets(self):
            return FakeSpreadsheets()

    rows = fetch_sheet_job_rows_from_env(service=FakeService())
    assert len(rows) == 1
    assert rows[0].sheet_row_id == "1"


def test_fetch_from_env_requires_vars(monkeypatch):
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_SHEET_ID", raising=False)
    try:
        fetch_sheet_job_rows_from_env(service=object())
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "GOOGLE_SERVICE_ACCOUNT_FILE" in str(exc)
