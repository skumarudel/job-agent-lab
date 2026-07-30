from pathlib import Path

from job_agent_lab.apply_tools import (
    get_job_for_apply,
    list_jobs_for_apply,
    load_base_cover_letter_tool,
    load_resume_text,
    save_cover_letter_to_db,
)
from job_agent_lab.db import JobRow, init_db, insert_pending_job, mark_job_ready
from job_agent_lab.job_id import job_id_from_link


def _seed_job(tmp_path: Path, *, applied: bool = False) -> tuple[Path, str]:
    db_path = tmp_path / "jobs.db"
    conn = init_db(db_path)
    link = (
        "https://example.com/jobs/applied"
        if applied
        else "https://example.com/jobs/open"
    )
    job_id = job_id_from_link(link)
    insert_pending_job(
        conn,
        job_id,
        JobRow(
            sheet_row_id="1",
            saved_date="2026-01-01",
            company="Acme",
            position="Data Engineer",
            location="Remote",
            job_link=link,
            application_status="Applied" if applied else "Not applied",
        ),
    )
    mark_job_ready(
        conn,
        job_id,
        "Build pipelines.",
        {
            "summary": "Build pipelines.",
            "key_requirements": ["Python ETL"],
            "important_skills": ["Python", "Airflow"],
            "role_family": "Data Engineer",
        },
    )
    conn.close()
    return db_path, job_id


def test_list_and_get_job_tools(tmp_path, monkeypatch):
    db_path, job_id = _seed_job(tmp_path)
    _seed_job(tmp_path, applied=True)  # same db - need same path
    # Re-seed applied into same db
    conn = init_db(db_path)
    applied_link = "https://example.com/jobs/applied-only"
    applied_id = job_id_from_link(applied_link)
    insert_pending_job(
        conn,
        applied_id,
        JobRow(
            sheet_row_id="2",
            saved_date="2026-01-02",
            company="Beta",
            position="DS",
            location="NYC",
            job_link=applied_link,
            application_status="Applied",
        ),
    )
    conn.close()

    monkeypatch.setenv("JOB_AGENT_DB_PATH", str(db_path))
    listed = list_jobs_for_apply(only_not_applied=True)
    assert listed["success"] is True
    assert listed["count"] == 1
    assert listed["jobs"][0]["job_id"] == job_id

    detail = get_job_for_apply(job_id)
    assert detail["success"] is True
    assert detail["job"]["company"] == "Acme"
    assert detail["job"]["role_family"] == "Data Engineer"
    assert "Python ETL" in detail["job"]["key_requirements"]


def test_save_cover_letter_tool(tmp_path, monkeypatch):
    db_path, job_id = _seed_job(tmp_path)
    monkeypatch.setenv("JOB_AGENT_DB_PATH", str(db_path))
    result = save_cover_letter_to_db(job_id, "Dear Hiring Manager,\n\nPolished.")
    assert result["success"] is True
    detail = get_job_for_apply(job_id)
    assert detail["job"]["cover_letter"].startswith("Dear Hiring Manager")


def test_load_resume_and_base_letter(tmp_path, monkeypatch):
    resume = tmp_path / "resume.md"
    resume.write_text("# Me\n\nPython and SQL experience.\n", encoding="utf-8")
    monkeypatch.setenv("RESUME_PATH", str(resume))

    loaded = load_resume_text()
    assert loaded["success"] is True
    assert "Python" in loaded["resume_text"]

    # Base letter from repo assets
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv(
        "BASE_COVER_LETTER_PATH",
        str(repo_root / "assets" / "cover_letter.docx"),
    )
    base = load_base_cover_letter_tool()
    assert base["success"] is True
    assert "software engineer" in base["base_cover_letter"].lower()


def test_build_apply_agent_variants():
    from job_agent_lab.apply_agent import build_apply_agent, resolve_model_id

    assert "ollama" in resolve_model_id("ollama")
    agent = build_apply_agent(variant="ollama", name="apply_ollama")
    assert agent.name == "apply_ollama"
    assert len(agent.tools) == 5
