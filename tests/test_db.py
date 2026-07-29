from job_agent_lab.db import init_db


def test_init_db_creates_jobs_table(tmp_path):
    db_path = tmp_path / "jobs.db"
    conn = init_db(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchall()
        assert rows == [("jobs",)]
    finally:
        conn.close()


def test_jobs_table_has_job_id_column(tmp_path):
    db_path = tmp_path / "jobs.db"
    conn = init_db(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "job_id" in cols
        assert "job_link" in cols
        assert "status" in cols
        assert "application_status" in cols
        assert "notes" in cols
        assert "interview_stage" in cols
    finally:
        conn.close()


def test_sync_stores_application_fields(tmp_path):
    from job_agent_lab.db import JobRow, insert_pending_job
    from job_agent_lab.job_id import job_id_from_link

    conn = init_db(tmp_path / "jobs.db")
    row = JobRow(
        sheet_row_id="1",
        saved_date="2026-01-01",
        company="Acme",
        position="Eng",
        location="Remote",
        job_link="https://example.com/jobs/1",
        application_status="Applied",
        notes="recruiter replied",
        interview_stage="Screen",
    )
    job_id = job_id_from_link(row.job_link)
    assert insert_pending_job(conn, job_id, row)
    saved = conn.execute(
        "SELECT application_status, notes, interview_stage, status FROM jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert saved == ("Applied", "recruiter replied", "Screen", "pending")
    conn.close()
