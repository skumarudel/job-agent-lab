from pathlib import Path

from docx import Document

from job_agent_lab.cover_letter import (
    load_base_cover_letter,
    process_cover_letters,
    tailor_cover_letter,
)
from job_agent_lab.db import (
    JobRow,
    init_db,
    insert_pending_job,
    list_jobs_needing_cover_letter,
    mark_job_ready,
    update_cover_letter,
)
from job_agent_lab.job_id import job_id_from_link


def _write_docx(path: Path, paragraphs: list[str]) -> Path:
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    doc.save(path)
    return path


def _ready_job(conn, link: str, company: str = "Acme", position: str = "Engineer") -> str:
    row = JobRow(
        sheet_row_id="1",
        saved_date="2026-01-01",
        company=company,
        position=position,
        location="Remote",
        job_link=link,
    )
    job_id = job_id_from_link(link)
    assert insert_pending_job(conn, job_id, row)
    mark_job_ready(
        conn,
        job_id,
        summary="Build reliable systems.",
        requirements=["5+ years of Python experience", "Strong SQL skills"],
    )
    return job_id


def test_load_and_tailor_include_base_and_job(tmp_path):
    path = _write_docx(
        tmp_path / "base.docx",
        [
            "Dear Hiring Manager,",
            "I am a software engineer with experience building APIs in Python.",
            "Sincerely,",
            "Applicant",
        ],
    )
    base = load_base_cover_letter(path)
    assert "software engineer" in base.lower()

    letter = tailor_cover_letter(
        base,
        company="Globex",
        position="Platform Engineer",
        requirements=["5+ years of Python experience", "Strong SQL skills"],
    )
    assert "Globex" in letter
    assert "Platform Engineer" in letter
    assert "software engineer with experience building APIs in Python" in letter
    assert "5+ years of Python experience" in letter
    # Does not invent employers/roles beyond base + job metadata
    assert "Staff Engineer at Netflix" not in letter


def test_process_updates_ready_jobs_and_skips_existing(tmp_path):
    conn = init_db(tmp_path / "jobs.db")
    base = _write_docx(
        tmp_path / "base.docx",
        ["I ship maintainable Python systems.", "Sincerely, Applicant"],
    )
    need_id = _ready_job(conn, "https://example.com/jobs/need-letter")
    skip_id = _ready_job(conn, "https://example.com/jobs/have-letter", company="Beta")
    update_cover_letter(conn, skip_id, "already written")

    assert [row[0] for row in list_jobs_needing_cover_letter(conn)] == [need_id]

    result = process_cover_letters(conn, base_letter_path=base)
    assert result["updated"] == [need_id]
    assert result["error"] == []

    saved = conn.execute(
        "SELECT cover_letter, status, summary FROM jobs WHERE job_id = ?",
        (need_id,),
    ).fetchone()
    assert saved[1] == "ready"
    assert saved[2] == "Build reliable systems."
    assert "Acme" in saved[0]
    assert "Engineer" in saved[0]
    assert "I ship maintainable Python systems." in saved[0]

    skipped = conn.execute(
        "SELECT cover_letter FROM jobs WHERE job_id = ?", (skip_id,)
    ).fetchone()
    assert skipped == ("already written",)
    conn.close()


def test_missing_base_marks_error_without_wiping_ready(tmp_path):
    conn = init_db(tmp_path / "jobs.db")
    job_id = _ready_job(conn, "https://example.com/jobs/missing-base")

    result = process_cover_letters(conn, base_letter_path=tmp_path / "nope.docx")
    assert result["updated"] == []
    assert result["error"] == [job_id]

    row = conn.execute(
        "SELECT status, summary, cover_letter FROM jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert row[0] == "ready"
    assert row[1] == "Build reliable systems."
    assert row[2] is None
    conn.close()


def test_repo_placeholder_cover_letter_loads():
    repo_root = Path(__file__).resolve().parents[1]
    path = repo_root / "assets" / "cover_letter.docx"
    text = load_base_cover_letter(path)
    assert "software engineer" in text.lower()
