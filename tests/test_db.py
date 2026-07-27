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
    finally:
        conn.close()
