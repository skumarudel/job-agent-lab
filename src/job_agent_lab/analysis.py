"""Structured job posting analysis via Pydantic and local Ollama."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from job_agent_lab.ollama_util import ollama_chat

RoleFamily = Literal[
    "Data Engineer",
    "Analytics Engineer",
    "Data Scientist",
    "Other",
]


class JobAnalysis(BaseModel):
    """Structured summary of what a job posting needs.

    Attributes:
        summary: Short overview of the role and posting.
        key_requirements: Must-have qualifications / responsibilities.
        important_skills: Skills needed to function day-to-day in the role.
        role_family: Closest family among DE / Analytics Eng / DS (or Other).
    """

    summary: str = Field(min_length=1)
    key_requirements: list[str] = Field(min_length=1)
    important_skills: list[str] = Field(min_length=1)
    role_family: RoleFamily = "Other"

    @field_validator("summary")
    @classmethod
    def _strip_summary(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("summary must be non-empty")
        return cleaned

    @field_validator("key_requirements", "important_skills")
    @classmethod
    def _clean_string_lists(cls, value: list[str]) -> list[str]:
        items = [str(item).strip() for item in value if str(item).strip()]
        if not items:
            raise ValueError("list must contain at least one non-empty string")
        return items


def job_analysis_provider() -> str:
    """Return ``ollama`` or ``heuristic`` from ``JOB_ANALYSIS_PROVIDER``."""
    value = os.environ.get("JOB_ANALYSIS_PROVIDER", "ollama").strip().lower()
    if value in {"ollama", "heuristic"}:
        return value
    raise ValueError(
        f"unsupported JOB_ANALYSIS_PROVIDER={value!r}; use 'ollama' or 'heuristic'"
    )


def job_analysis_to_requirements_payload(analysis: JobAnalysis) -> dict[str, Any]:
    """Serialize ``JobAnalysis`` for ``jobs.requirements_json``."""
    return analysis.model_dump()


def parse_stored_job_analysis(
    summary: str,
    requirements_json: str,
) -> JobAnalysis:
    """Rebuild a ``JobAnalysis`` from SQLite ``summary`` + ``requirements_json``.

    Supports the structured dict format and a legacy JSON list of requirement
    strings (pre-structured storage).

    Args:
        summary: Value from ``jobs.summary``.
        requirements_json: Value from ``jobs.requirements_json``.

    Returns:
        A validated ``JobAnalysis``.
    """
    try:
        raw = json.loads(requirements_json or "[]")
    except json.JSONDecodeError:
        raw = []

    if isinstance(raw, dict):
        payload = dict(raw)
        if not payload.get("summary"):
            payload["summary"] = summary or payload.get("summary") or "Job posting"
        return JobAnalysis.model_validate(payload)

    requirements = [str(item) for item in raw] if isinstance(raw, list) else []
    if not requirements and (summary or "").strip():
        requirements = [summary.strip()]
    if not requirements:
        requirements = ["See job posting"]
    return JobAnalysis(
        summary=(summary or "").strip() or "Job posting",
        key_requirements=requirements,
        important_skills=requirements[:5],
        role_family="Other",
    )


def build_job_analysis_prompt(page_text: str) -> str:
    """Build the user prompt that asks Ollama for JobAnalysis JSON."""
    truncated = page_text.strip()
    if len(truncated) > 12000:
        truncated = truncated[:12000] + "\n...[truncated]..."
    schema = (
        '{"summary":"string","key_requirements":["string"],'
        '"important_skills":["string"],'
        '"role_family":"Data Engineer|Analytics Engineer|Data Scientist|Other"}'
    )
    return (
        "Analyze this job posting for a candidate who targets Data Engineer, "
        "Analytics Engineer, and Data Scientist roles.\n"
        "Return ONLY valid JSON matching this schema (no markdown):\n"
        f"{schema}\n\n"
        "Guidelines:\n"
        "- summary: 2-4 sentences on the role and what success looks like.\n"
        "- key_requirements: must-have qualifications and core responsibilities.\n"
        "- important_skills: the most important skills needed to function in the job.\n"
        "- role_family: closest of Data Engineer, Analytics Engineer, Data Scientist, "
        "or Other.\n"
        "- Be faithful to the posting; do not invent employer-specific claims.\n\n"
        f"Job posting text:\n{truncated}\n"
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output, tolerating markdown fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("Ollama JSON must be an object")
    return data


def analyze_job_text_with_ollama(
    page_text: str,
    *,
    api_base: str | None = None,
    model: str | None = None,
    client: httpx.Client | None = None,
    timeout: float = 120.0,
) -> JobAnalysis:
    """Analyze job page text with Ollama into a ``JobAnalysis``.

    Args:
        page_text: Visible text from the scraped posting.
        api_base: Ollama base URL override.
        model: Model id override.
        client: Optional ``httpx.Client`` for tests.
        timeout: Request timeout in seconds.

    Returns:
        Validated ``JobAnalysis``.

    Raises:
        ValueError: If page text is empty or the response cannot be validated.
        httpx.HTTPError: If the Ollama HTTP call fails.
        ValidationError: If JSON does not match ``JobAnalysis``.
    """
    cleaned = (page_text or "").strip()
    if not cleaned:
        raise ValueError("page text is empty")

    content = ollama_chat(
        messages=[
            {
                "role": "system",
                "content": (
                    "You extract structured job requirements as JSON only. "
                    "No markdown, no preamble."
                ),
            },
            {"role": "user", "content": build_job_analysis_prompt(cleaned)},
        ],
        api_base=api_base,
        model=model,
        client=client,
        timeout=timeout,
        format_json=True,
    )
    try:
        payload = _extract_json_object(content)
        return JobAnalysis.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise ValueError(f"invalid job analysis JSON from Ollama: {exc}") from exc


def analyze_job_text_heuristic(page_text: str) -> JobAnalysis:
    """Heuristic fallback analyzer (no LLM) producing a ``JobAnalysis``.

    Args:
        page_text: Visible text from the scraped posting.

    Returns:
        ``JobAnalysis`` built from simple text heuristics.

    Raises:
        ValueError: If ``page_text`` is empty.
    """
    from job_agent_lab.scrape import build_summary_and_requirements

    summary, requirements = build_summary_and_requirements(page_text)
    skills = requirements[:8] if requirements else [summary]
    return JobAnalysis(
        summary=summary,
        key_requirements=requirements,
        important_skills=skills,
        role_family="Other",
    )
