"""Tailor cover letters from a base ``.docx``, optionally via local Ollama."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from docx import Document

from job_agent_lab.db import list_jobs_needing_cover_letter, update_cover_letter

DEFAULT_COVER_LETTER_PATH = Path("assets/cover_letter.docx")
DEFAULT_OLLAMA_API_BASE = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "ollama_chat/gemma4:e4b-mlx"

RewriteFn = Callable[..., str]


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


def normalize_ollama_model(model: str) -> str:
    """Strip LiteLLM-style prefixes so Ollama receives a native model tag.

    Args:
        model: Model id such as ``ollama_chat/gemma4:e4b-mlx`` or ``gemma4:e4b-mlx``.

    Returns:
        Model tag for the Ollama API (e.g. ``gemma4:e4b-mlx``).
    """
    name = (model or "").strip()
    for prefix in ("ollama_chat/", "ollama/"):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


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
) -> str:
    """Build a tailored letter from the base text plus job metadata (no LLM).

    Keeps the base letter body intact. Adds an application-specific opening
    that names company/position and optional interest lines drawn from the
    posting requirements. Does not invent work experience beyond the base.

    Args:
        base_text: Full text from ``load_base_cover_letter``.
        company: Employer name from the sheet/DB.
        position: Role title from the sheet/DB.
        requirements: Optional requirement strings from the posting.
        summary: Optional job summary (unused by the heuristic path; accepted
            for a shared call signature with the Ollama rewriter).

    Returns:
        Tailored cover letter text.

    Raises:
        ValueError: If ``base_text`` is empty/whitespace-only.
    """
    _ = summary  # shared signature with LLM rewriter
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


def build_ollama_rewrite_prompt(
    base_text: str,
    *,
    company: str,
    position: str,
    summary: str = "",
    requirements: list[str] | None = None,
) -> str:
    """Build the user prompt for an Ollama cover-letter rewrite.

    Args:
        base_text: Base letter text (source of allowed experience).
        company: Employer name.
        position: Role title.
        summary: Job posting summary.
        requirements: Structured requirements from the posting.

    Returns:
        Prompt string for the chat ``user`` message.
    """
    req_block = "\n".join(f"- {item}" for item in (requirements or []) if str(item).strip())
    if not req_block:
        req_block = "- (none provided)"
    return (
        "Rewrite the cover letter for this job application.\n"
        "Rules:\n"
        "- Use ONLY experience, skills, and claims present in the base letter.\n"
        "- Do NOT invent employers, degrees, titles, or achievements.\n"
        "- Customize wording for the company and position.\n"
        "- You may reference the summary/requirements only to align emphasis.\n"
        "- Return only the final cover letter text (no preamble).\n\n"
        f"Company: {(company or '').strip() or 'unknown'}\n"
        f"Position: {(position or '').strip() or 'unknown'}\n"
        f"Summary:\n{(summary or '').strip() or '(none)'}\n\n"
        f"Requirements:\n{req_block}\n\n"
        f"Base letter:\n{base_text.strip()}\n"
    )


def rewrite_cover_letter_with_ollama(
    base_text: str,
    *,
    company: str,
    position: str,
    summary: str = "",
    requirements: list[str] | None = None,
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
        requirements: Requirement strings from SQLite.
        api_base: Ollama base URL (env ``OLLAMA_API_BASE`` if omitted).
        model: Model id (env ``OLLAMA_MODEL`` if omitted). May include
            ``ollama_chat/`` prefix.
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

    base = (api_base or os.environ.get("OLLAMA_API_BASE") or DEFAULT_OLLAMA_API_BASE).rstrip(
        "/"
    )
    raw_model = model or os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
    ollama_model = normalize_ollama_model(raw_model)
    if not ollama_model:
        raise ValueError("OLLAMA_MODEL is empty")

    payload: dict[str, Any] = {
        "model": ollama_model,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a careful cover-letter editor. Never invent experience."
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
                ),
            },
        ],
    }

    url = f"{base}/api/chat"
    if client is None:
        with httpx.Client(timeout=timeout) as owned:
            response = owned.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
    else:
        response = client.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()

    message = data.get("message") or {}
    content = (message.get("content") or "").strip()
    if not content:
        raise ValueError("Ollama returned an empty cover letter")
    return content


def process_cover_letters(
    conn,
    *,
    base_letter_path: str | Path = DEFAULT_COVER_LETTER_PATH,
    provider: str | None = None,
    rewrite_fn: RewriteFn | None = None,
    ollama_client: httpx.Client | None = None,
) -> dict[str, list[str]]:
    """Write tailored cover letters for ready jobs that are missing one.

    Loads the base ``.docx`` once. Failures for individual jobs are collected
    and do not stop the batch. Pipeline ``status`` is left as ``ready`` so a
    letter failure does not re-trigger scraping.

    Args:
        conn: Open SQLite connection from ``init_db``.
        base_letter_path: Path to ``assets/cover_letter.docx`` (or a test fixture).
        provider: ``ollama`` or ``heuristic``. Defaults to ``COVER_LETTER_PROVIDER``.
        rewrite_fn: Optional callable used instead of the built-in rewriter
            (tests). Signature matches ``tailor_cover_letter`` /
            ``rewrite_cover_letter_with_ollama`` keyword args.
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
        ) -> str:
            return rewrite_cover_letter_with_ollama(
                text,
                company=company,
                position=position,
                summary=summary,
                requirements=requirements,
                client=ollama_client,
            )

    else:
        raise ValueError(f"unsupported cover letter provider: {selected!r}")

    for job_id, company, position, summary, requirements_json in jobs:
        try:
            try:
                requirements = json.loads(requirements_json or "[]")
            except json.JSONDecodeError:
                requirements = []
            if not isinstance(requirements, list):
                requirements = []
            letter = rewriter(
                base_text,
                company=company,
                position=position,
                requirements=[str(item) for item in requirements],
                summary=summary or "",
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
