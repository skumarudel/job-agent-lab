"""Tailor cover letters from a base ``.docx`` without inventing experience."""

from __future__ import annotations

import json
from pathlib import Path

from docx import Document

from job_agent_lab.db import list_jobs_needing_cover_letter, update_cover_letter

DEFAULT_COVER_LETTER_PATH = Path("assets/cover_letter.docx")


def load_base_cover_letter(path: str | Path = DEFAULT_COVER_LETTER_PATH) -> str:
    """Load plain text paragraphs from a base cover letter ``.docx``.

    Args:
        path: Filesystem path to the Word document.

    Returns:
        Non-empty letter text with paragraphs separated by blank lines.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If the document has no usable paragraph text.
    """
    doc_path = Path(path)
    if not doc_path.is_file():
        raise FileNotFoundError(f"base cover letter not found: {doc_path}")

    document = Document(doc_path)
    paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    if not paragraphs:
        raise ValueError(f"base cover letter is empty: {doc_path}")
    return "\n\n".join(paragraphs)


def tailor_cover_letter(
    base_text: str,
    *,
    company: str,
    position: str,
    requirements: list[str] | None = None,
) -> str:
    """Build a tailored letter from the base text plus job metadata.

    Keeps the base letter body intact. Adds an application-specific opening
    that names company/position and optional interest lines drawn from the
    posting requirements. Does not invent work experience beyond the base.

    Args:
        base_text: Full text from ``load_base_cover_letter``.
        company: Employer name from the sheet/DB.
        position: Role title from the sheet/DB.
        requirements: Optional requirement strings from the posting.

    Returns:
        Tailored cover letter text.

    Raises:
        ValueError: If ``base_text`` is empty/whitespace-only.
    """
    body = (base_text or "").strip()
    if not body:
        raise ValueError("base cover letter text is empty")

    company_label = (company or "").strip() or "your company"
    position_label = (position or "").strip() or "this role"

    opening = (
        f"I am writing to apply for the {position_label} position at "
        f"{company_label}."
    )

    interest_lines: list[str] = []
    for item in requirements or []:
        cleaned = (item or "").strip()
        if len(cleaned) < 12:
            continue
        interest_lines.append(cleaned)
        if len(interest_lines) >= 3:
            break

    parts = [opening, body]
    if interest_lines:
        bullets = "\n".join(f"- {line}" for line in interest_lines)
        parts.append(
            "Based on the job posting, I am particularly interested in relating "
            "my background to the following areas:\n"
            f"{bullets}"
        )
    return "\n\n".join(parts)


def process_cover_letters(
    conn,
    *,
    base_letter_path: str | Path = DEFAULT_COVER_LETTER_PATH,
) -> dict[str, list[str]]:
    """Write tailored cover letters for ready jobs that are missing one.

    Loads the base ``.docx`` once. Failures for individual jobs are collected
    and do not stop the batch. Pipeline ``status`` is left as ``ready`` so a
    letter failure does not re-trigger scraping.

    Args:
        conn: Open SQLite connection from ``init_db``.
        base_letter_path: Path to ``assets/cover_letter.docx`` (or a test fixture).

    Returns:
        Dict with ``updated`` and ``error`` lists of ``job_id`` values.
    """
    result: dict[str, list[str]] = {"updated": [], "error": []}
    jobs = list_jobs_needing_cover_letter(conn)
    if not jobs:
        return result

    try:
        base_text = load_base_cover_letter(base_letter_path)
    except (OSError, ValueError):
        result["error"] = [job_id for job_id, *_rest in jobs]
        return result

    for job_id, company, position, _summary, requirements_json in jobs:
        try:
            try:
                requirements = json.loads(requirements_json or "[]")
            except json.JSONDecodeError:
                requirements = []
            if not isinstance(requirements, list):
                requirements = []
            letter = tailor_cover_letter(
                base_text,
                company=company,
                position=position,
                requirements=[str(item) for item in requirements],
            )
            update_cover_letter(conn, job_id, letter)
            result["updated"].append(job_id)
        except (OSError, ValueError, TypeError):
            result["error"].append(job_id)
        except Exception:  # noqa: BLE001 — isolate unexpected per-job failures
            result["error"].append(job_id)

    return result
