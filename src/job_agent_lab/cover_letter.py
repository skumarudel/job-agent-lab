"""Tailor cover letters from a base ``.docx``, optionally via local Ollama."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import httpx
from docx import Document

from job_agent_lab.analysis import JobAnalysis, parse_stored_job_analysis
from job_agent_lab.db import list_jobs_needing_cover_letter, update_cover_letter
from job_agent_lab.ollama_util import (
    DEFAULT_OLLAMA_API_BASE,
    DEFAULT_OLLAMA_MODEL,
    normalize_ollama_model,
    ollama_chat,
)

DEFAULT_COVER_LETTER_PATH = Path("assets/cover_letter.docx")

RewriteFn = Callable[..., str]

# Re-export for callers/tests that imported these from this module.
__all__ = [
    "DEFAULT_COVER_LETTER_PATH",
    "DEFAULT_OLLAMA_API_BASE",
    "DEFAULT_OLLAMA_MODEL",
    "TARGET_ROLE_FAMILIES",
    "build_ollama_rewrite_prompt",
    "cover_letter_provider",
    "load_base_cover_letter",
    "normalize_ollama_model",
    "process_cover_letters",
    "rewrite_cover_letter_with_ollama",
    "tailor_cover_letter",
]


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


def cover_letter_provider() -> str:
    """Return the configured cover-letter provider from the environment.

    Returns:
        ``ollama`` or ``heuristic``. Defaults to ``ollama``.
    """
    value = os.environ.get("COVER_LETTER_PROVIDER", "ollama").strip().lower()
    if value in {"ollama", "heuristic"}:
        return value
    raise ValueError(
        f"unsupported COVER_LETTER_PROVIDER={value!r}; use 'ollama' or 'heuristic'"
    )


def tailor_cover_letter(
    base_text: str,
    *,
    company: str,
    position: str,
    requirements: list[str] | None = None,
    summary: str = "",
    analysis: JobAnalysis | None = None,
) -> str:
    """Build a tailored letter from the base text plus job metadata (no LLM).

    Keeps the base letter body intact. Adds an application-specific opening
    that names company/position and optional interest lines drawn from the
    posting analysis. Does not invent work experience beyond the base.

    Args:
        base_text: Full text from ``load_base_cover_letter``.
        company: Employer name from the sheet/DB.
        position: Role title from the sheet/DB.
        requirements: Optional requirement strings from the posting.
        summary: Optional job summary (shared signature with Ollama rewriter).
        analysis: Optional structured ``JobAnalysis`` (preferred when present).

    Returns:
        Tailored cover letter text.

    Raises:
        ValueError: If ``base_text`` is empty/whitespace-only.
    """
    _ = summary
    body = (base_text or "").strip()
    if not body:
        raise ValueError("base cover letter text is empty")

    company_label = (company or "").strip() or "your company"
    position_label = (position or "").strip() or "this role"
    opening = (
        f"I am writing to apply for the {position_label} position at "
        f"{company_label}."
    )

    interest_source = list(requirements or [])
    if analysis is not None:
        interest_source = list(analysis.key_requirements) + list(analysis.important_skills)

    interest_lines: list[str] = []
    seen: set[str] = set()
    for item in interest_source:
        cleaned = (item or "").strip()
        if len(cleaned) < 12:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
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


TARGET_ROLE_FAMILIES = (
    "Data Engineer",
    "Analytics Engineer",
    "Data Scientist",
)


def build_ollama_rewrite_prompt(
    base_text: str,
    *,
    company: str,
    position: str,
    summary: str = "",
    requirements: list[str] | None = None,
    analysis: JobAnalysis | None = None,
) -> str:
    """Build the user prompt for an Ollama cover-letter rewrite.

    Prefers a structured ``JobAnalysis`` from the scrape step when provided.

    Args:
        base_text: Base letter text (source of allowed experience).
        company: Employer name.
        position: Role title.
        summary: Job posting summary (used if ``analysis`` is omitted).
        requirements: Legacy requirement string list.
        analysis: Preferred structured analysis from the scrape step.

    Returns:
        Prompt string for the chat ``user`` message.
    """
    if analysis is not None:
        summary_text = analysis.summary
        req_block = "\n".join(f"- {item}" for item in analysis.key_requirements)
        skills_block = "\n".join(f"- {item}" for item in analysis.important_skills)
        role_family = analysis.role_family
    else:
        summary_text = (summary or "").strip() or "(none)"
        req_block = "\n".join(
            f"- {item}" for item in (requirements or []) if str(item).strip()
        )
        skills_block = "- (none provided)"
        role_family = "unknown"
    if not req_block:
        req_block = "- (none provided)"

    families = ", ".join(TARGET_ROLE_FAMILIES)
    return (
        "Rewrite the cover letter for this job application.\n"
        "Rules:\n"
        "- Use ONLY experience, skills, and claims present in the base letter.\n"
        "- Do NOT invent employers, degrees, titles, or achievements.\n"
        "- Customize wording for the company and position.\n"
        f"- This applicant targets three role families only: {families}.\n"
        "- Prefer the analyzed role_family when aligning emphasis; if it is Other, "
        "infer the closest family from the position and analysis.\n"
        "- Emphasize language and themes appropriate to that family, for example:\n"
        "  - Data Engineer: pipelines, ETL/ELT, warehousing, orchestration, reliability\n"
        "  - Analytics Engineer: dbt/modeling, semantic layers, analytics-ready data\n"
        "  - Data Scientist: modeling/analysis, experimentation, statistical insight\n"
        "- Use the job analysis summary, key requirements, and important skills only "
        "to choose emphasis; do not claim skills absent from the base letter.\n"
        "- Return only the final cover letter text (no preamble).\n\n"
        f"Company: {(company or '').strip() or 'unknown'}\n"
        f"Position: {(position or '').strip() or 'unknown'}\n"
        f"Analyzed role family: {role_family}\n"
        f"Job analysis summary:\n{summary_text}\n\n"
        f"Key requirements:\n{req_block}\n\n"
        f"Important skills:\n{skills_block}\n\n"
        f"Base letter:\n{base_text.strip()}\n"
    )


def rewrite_cover_letter_with_ollama(
    base_text: str,
    *,
    company: str,
    position: str,
    summary: str = "",
    requirements: list[str] | None = None,
    analysis: JobAnalysis | None = None,
    api_base: str | None = None,
    model: str | None = None,
    client: httpx.Client | None = None,
    timeout: float = 120.0,
) -> str:
    """Rewrite a cover letter using a local Ollama chat model.

    Args:
        base_text: Base letter text from the ``.docx``.
        company: Employer name.
        position: Role title.
        summary: Job summary from SQLite.
        requirements: Requirement strings from SQLite (legacy).
        analysis: Structured ``JobAnalysis`` from the scrape step (preferred).
        api_base: Ollama base URL (env ``OLLAMA_API_BASE`` if omitted).
        model: Model id (env ``OLLAMA_MODEL`` if omitted).
        client: Optional ``httpx.Client`` (tests inject a mock transport).
        timeout: Request timeout in seconds.

    Returns:
        Rewritten cover letter text from the model.

    Raises:
        ValueError: If base text is empty or the model returns empty content.
        httpx.HTTPError: If the Ollama HTTP call fails.
    """
    if not (base_text or "").strip():
        raise ValueError("base cover letter text is empty")

    return ollama_chat(
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a careful cover-letter editor for Data Engineer, "
                    "Analytics Engineer, and Data Scientist applications. "
                    "Never invent experience."
                ),
            },
            {
                "role": "user",
                "content": build_ollama_rewrite_prompt(
                    base_text,
                    company=company,
                    position=position,
                    summary=summary,
                    requirements=requirements,
                    analysis=analysis,
                ),
            },
        ],
        api_base=api_base,
        model=model,
        client=client,
        timeout=timeout,
    )


def process_cover_letters(
    conn,
    *,
    base_letter_path: str | Path = DEFAULT_COVER_LETTER_PATH,
    provider: str | None = None,
    rewrite_fn: RewriteFn | None = None,
    ollama_client: httpx.Client | None = None,
) -> dict[str, list[str]]:
    """Write tailored cover letters for ready jobs that are missing one.

    Uses stored job analysis (summary + structured requirements JSON) as input
    to the rewriter together with the base letter.

    Args:
        conn: Open SQLite connection from ``init_db``.
        base_letter_path: Path to ``assets/cover_letter.docx`` (or a test fixture).
        provider: ``ollama`` or ``heuristic``. Defaults to ``COVER_LETTER_PROVIDER``.
        rewrite_fn: Optional callable override for tests.
        ollama_client: Optional shared ``httpx.Client`` for Ollama calls.

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

    selected = (provider or cover_letter_provider()).strip().lower()
    if rewrite_fn is not None:
        rewriter: RewriteFn = rewrite_fn
    elif selected == "heuristic":
        rewriter = tailor_cover_letter
    elif selected == "ollama":

        def rewriter(
            text: str,
            *,
            company: str,
            position: str,
            requirements: list[str] | None = None,
            summary: str = "",
            analysis: JobAnalysis | None = None,
        ) -> str:
            return rewrite_cover_letter_with_ollama(
                text,
                company=company,
                position=position,
                summary=summary,
                requirements=requirements,
                analysis=analysis,
                client=ollama_client,
            )

    else:
        raise ValueError(f"unsupported cover letter provider: {selected!r}")

    for job_id, company, position, summary, requirements_json in jobs:
        try:
            analysis = parse_stored_job_analysis(summary or "", requirements_json or "[]")
            letter = rewriter(
                base_text,
                company=company,
                position=position,
                requirements=list(analysis.key_requirements),
                summary=analysis.summary,
                analysis=analysis,
            )
            if not (letter or "").strip():
                raise ValueError("cover letter rewriter returned empty text")
            update_cover_letter(conn, job_id, letter.strip())
            result["updated"].append(job_id)
        except (OSError, ValueError, TypeError, httpx.HTTPError):
            result["error"].append(job_id)
        except Exception:  # noqa: BLE001 — isolate unexpected per-job failures
            result["error"].append(job_id)

    return result
