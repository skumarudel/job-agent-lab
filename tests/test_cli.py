from pathlib import Path
from unittest.mock import patch

from job_agent_lab.cli import (
    format_job_list,
    format_job_show,
    format_run_summary,
    main,
    resolve_db_path,
    run_pipeline,
)
from job_agent_lab.db import (
    JobRow,
    get_job,
    init_db,
    insert_pending_job,
    list_ready_jobs,
    mark_job_ready,
    update_cover_letter,
)
from job_agent_lab.job_id import job_id_from_link


def test_resolve_db_path_prefers_cli_then_env(monkeypatch, tmp_path):
    monkeypatch.delenv("JOB_AGENT_DB_PATH", raising=False)
    assert resolve_db_path(None) == Path("data/jobs.db")
    monkeypatch.setenv("JOB_AGENT_DB_PATH", str(tmp_path / "env.db"))
    assert resolve_db_path(None) == tmp_path / "env.db"
    assert resolve_db_path(str(tmp_path / "cli.db")) == tmp_path / "cli.db"


def test_run_pipeline_calls_steps_in_order(tmp_path):
    db_path = tmp_path / "jobs.db"
    calls: list[str] = []

    with (
        patch("job_agent_lab.cli.fetch_sheet_job_rows_from_env", return_value=[]) as fetch,
        patch(
            "job_agent_lab.cli.sync_new_jobs_from_rows",
            side_effect=lambda conn, rows: calls.append("sync") or ["id1"],
        ),
        patch(
            "job_agent_lab.cli.process_pending_jobs",
            side_effect=lambda conn: calls.append("analyze")
            or {"ready": ["id1"], "error": []},
        ),
        patch(
            "job_agent_lab.cli.process_cover_letters",
            side_effect=lambda conn: calls.append("letters")
            or {"updated": ["id1"], "error": []},
        ),
    ):
        result = run_pipeline(db_path)

    fetch.assert_called_once()
    assert calls == ["sync", "analyze", "letters"]
    assert result["synced"] == ["id1"]
    assert "Synced new jobs: 1" in format_run_summary(result)


def test_list_and_show_commands(tmp_path, capsys):
    db_path = tmp_path / "jobs.db"
    conn = init_db(db_path)
    link = "https://example.com/jobs/cli-show"
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
            application_status="Not applied",
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
    update_cover_letter(conn, job_id, "Dear Hiring Manager,\n\nHello.")
    conn.close()

    assert main(["--db", str(db_path), "list"]) == 0
    listed = capsys.readouterr().out
    assert job_id in listed
    assert "Acme" in listed
    assert "yes" in listed

    assert main(["--db", str(db_path), "show", job_id]) == 0
    shown = capsys.readouterr().out
    assert "Build pipelines." in shown
    assert "Python ETL" in shown
    assert "Airflow" in shown
    assert "Dear Hiring Manager" in shown


def test_show_missing_job_returns_2(tmp_path, capsys):
    db_path = tmp_path / "jobs.db"
    init_db(db_path).close()
    assert main(["--db", str(db_path), "show", "missing"]) == 2
    err = capsys.readouterr().err
    assert "job not found" in err


def test_list_ready_jobs_helper(tmp_path):
    conn = init_db(tmp_path / "jobs.db")
    link = "https://example.com/jobs/ready-list"
    job_id = job_id_from_link(link)
    insert_pending_job(
        conn,
        job_id,
        JobRow(
            sheet_row_id="1",
            saved_date="2026-01-01",
            company="Beta",
            position="DS",
            location="Remote",
            job_link=link,
            application_status="Not applied",
        ),
    )
    mark_job_ready(conn, job_id, "summary", ["req"])
    rows = list_ready_jobs(conn)
    assert rows == [(job_id, "Beta", "DS", False)]
    job = get_job(conn, job_id)
    assert job is not None
    assert job["company"] == "Beta"
    text = format_job_list(rows)
    assert "has_cover_letter" in text
    shown = format_job_show(job)
    assert "not generated yet" in shown
    conn.close()
