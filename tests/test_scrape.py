import json

import httpx

from job_agent_lab.analysis import JobAnalysis, analyze_job_text_with_ollama
from job_agent_lab.db import (
    JobRow,
    init_db,
    insert_pending_job,
    list_jobs_to_process,
    mark_job_error,
    mark_job_ready,
)
from job_agent_lab.job_id import job_id_from_link
from job_agent_lab.scrape import (
    build_summary_and_requirements,
    html_to_visible_text,
    process_pending_jobs,
)


SAMPLE_HTML = """
<html><head><script>var x=1</script><style>body{}</style></head>
<body>
<h1>Software Engineer</h1>
<p>We are hiring a software engineer to build reliable data platforms for our customers across multiple regions.</p>
<ul>
  <li>5+ years of experience with Python</li>
  <li>Must have experience with SQL and cloud services</li>
  <li>Bachelor's degree in Computer Science or equivalent</li>
</ul>
<p>Nice to have: Kubernetes experience</p>
</body></html>
"""


def _insert(conn, link: str, company: str = "Acme") -> str:
    row = JobRow(
        sheet_row_id="1",
        saved_date="2026-01-01",
        company=company,
        position="Eng",
        location="Remote",
        job_link=link,
    )
    job_id = job_id_from_link(link)
    assert insert_pending_job(conn, job_id, row)
    return job_id


def test_html_to_visible_text_strips_script_and_style():
    text = html_to_visible_text(SAMPLE_HTML)
    assert "var x" not in text
    assert "Software Engineer" in text
    assert "5+ years of experience with Python" in text


def test_build_summary_and_requirements_extracts_bullets():
    text = html_to_visible_text(SAMPLE_HTML)
    summary, requirements = build_summary_and_requirements(text)
    assert "software engineer" in summary.lower()
    assert any("Python" in item for item in requirements)
    assert any("SQL" in item for item in requirements)


def test_build_summary_rejects_empty_text():
    try:
        build_summary_and_requirements("   ")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "empty" in str(exc).lower()


def test_process_pending_marks_ready_with_summary(tmp_path):
    conn = init_db(tmp_path / "jobs.db")
    job_id = _insert(conn, "https://example.com/jobs/ok")

    def fake_fetch(url: str) -> str:
        assert url.endswith("/ok")
        return SAMPLE_HTML

    result = process_pending_jobs(
        conn, fetch_html=fake_fetch, provider="heuristic"
    )
    assert result["ready"] == [job_id]
    assert result["error"] == []

    row = conn.execute(
        "SELECT status, summary, requirements_json, error_message, cover_letter "
        "FROM jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert row[0] == "ready"
    assert row[1] and "software engineer" in row[1].lower()
    payload = json.loads(row[2])
    assert payload["key_requirements"]
    assert payload["important_skills"]
    assert payload["role_family"] == "Other"
    assert row[3] is None
    assert row[4] is None
    conn.close()


def test_process_isolates_failures_and_continues(tmp_path):
    conn = init_db(tmp_path / "jobs.db")
    bad_id = _insert(conn, "https://example.com/jobs/bad")
    good_id = _insert(conn, "https://example.com/jobs/good", company="Beta")

    def fake_fetch(url: str) -> str:
        if url.endswith("/bad"):
            raise RuntimeError("browser boom")
        return SAMPLE_HTML

    result = process_pending_jobs(
        conn, fetch_html=fake_fetch, provider="heuristic"
    )
    assert set(result["ready"]) == {good_id}
    assert set(result["error"]) == {bad_id}

    bad = conn.execute(
        "SELECT status, error_message FROM jobs WHERE job_id = ?", (bad_id,)
    ).fetchone()
    assert bad == ("error", "browser boom")

    good = conn.execute(
        "SELECT status FROM jobs WHERE job_id = ?", (good_id,)
    ).fetchone()
    assert good == ("ready",)
    conn.close()


def test_process_skips_ready_and_retries_error(tmp_path):
    conn = init_db(tmp_path / "jobs.db")
    ready_id = _insert(conn, "https://example.com/jobs/ready")
    mark_job_ready(conn, ready_id, "already done", ["req"])

    error_id = _insert(conn, "https://example.com/jobs/retry", company="Gamma")
    mark_job_error(conn, error_id, "previous failure")

    assert list_jobs_to_process(conn) == [(error_id, "https://example.com/jobs/retry")]

    calls: list[str] = []

    def fake_fetch(url: str) -> str:
        calls.append(url)
        return SAMPLE_HTML

    result = process_pending_jobs(
        conn, fetch_html=fake_fetch, provider="heuristic"
    )
    assert calls == ["https://example.com/jobs/retry"]
    assert result["ready"] == [error_id]

    still_ready = conn.execute(
        "SELECT summary FROM jobs WHERE job_id = ?", (ready_id,)
    ).fetchone()
    assert still_ready == ("already done",)
    conn.close()


def test_empty_page_text_marks_error(tmp_path):
    conn = init_db(tmp_path / "jobs.db")
    job_id = _insert(conn, "https://example.com/jobs/empty")

    result = process_pending_jobs(
        conn,
        fetch_html=lambda _url: "<html><body><script>x</script></body></html>",
        provider="heuristic",
    )
    assert result["error"] == [job_id]
    status, message = conn.execute(
        "SELECT status, error_message FROM jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    assert status == "error"
    assert message
    conn.close()


def test_process_with_ollama_analysis_stores_structured_json(tmp_path):
    conn = init_db(tmp_path / "jobs.db")
    job_id = _insert(conn, "https://example.com/jobs/llm")

    payload = {
        "summary": "Build data platforms.",
        "key_requirements": ["Python pipelines", "SQL warehousing"],
        "important_skills": ["Python", "Airflow", "SQL"],
        "role_family": "Data Engineer",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": json.dumps(payload)}},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = process_pending_jobs(
            conn,
            fetch_html=lambda _url: SAMPLE_HTML,
            provider="ollama",
            ollama_client=client,
        )

    assert result["ready"] == [job_id]
    row = conn.execute(
        "SELECT summary, requirements_json, status FROM jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert row[0] == "Build data platforms."
    assert json.loads(row[1]) == payload
    assert row[2] == "ready"
    conn.close()


def test_analyze_job_text_with_ollama_validates_pydantic():
    payload = {
        "summary": "Analytics role focused on dbt models.",
        "key_requirements": ["dbt experience", "SQL fluency"],
        "important_skills": ["dbt", "SQL", "Looker"],
        "role_family": "Analytics Engineer",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["format"] == "json"
        assert body["model"] == "gemma4:e4b-mlx"
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": json.dumps(payload)}},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        analysis = analyze_job_text_with_ollama(
            "Job posting about analytics engineering",
            model="ollama_chat/gemma4:e4b-mlx",
            client=client,
        )

    assert isinstance(analysis, JobAnalysis)
    assert analysis.role_family == "Analytics Engineer"
    assert "dbt" in analysis.important_skills
