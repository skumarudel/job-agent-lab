"""Scrape job pages with Selenium and analyze requirements (Ollama or heuristic)."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import httpx
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options

from job_agent_lab.analysis import (
    JobAnalysis,
    analyze_job_text_heuristic,
    analyze_job_text_with_ollama,
    job_analysis_provider,
)
from job_agent_lab.db import list_jobs_to_process, mark_job_error, mark_job_ready

FetchHtml = Callable[[str], str]
AnalyzeFn = Callable[[str], JobAnalysis]

_REQUIREMENT_HINT = re.compile(
    r"\b("
    r"require|requirement|qualificat|must have|must be|responsibilit|"
    r"you will|you should|experience with|years of|bachelor|master|"
    r"preferred|nice to have|minimum"
    r")\b",
    re.IGNORECASE,
)
_BULLET_PREFIX = re.compile(r"^[\-\*\u2022\u2023\u25E6\u2043\d]+[.)\]]\s*")


def create_headless_chrome() -> webdriver.Chrome:
    """Create a headless Chrome WebDriver via Selenium Manager.

    Returns:
        A configured ``webdriver.Chrome`` instance. Caller must quit it.
    """
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1280,1696")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(options=options)


def fetch_rendered_html(url: str, *, driver: Any | None = None) -> str:
    """Load a URL in Chrome and return the fully rendered page HTML.

    Uses Selenium so JavaScript-heavy job boards can populate content.
    If ``driver`` is omitted, a headless Chrome is created and quit after
    the fetch.

    Args:
        url: Job posting URL to open.
        driver: Optional existing WebDriver (for reuse or tests).

    Returns:
        Page ``page_source`` after navigation.

    Raises:
        WebDriverException: If the browser cannot load the page.
        ValueError: If ``url`` is empty.
    """
    if not url or not str(url).strip():
        raise ValueError("url must be a non-empty string")

    owns_driver = driver is None
    browser = driver or create_headless_chrome()
    try:
        browser.get(url)
        return browser.page_source or ""
    finally:
        if owns_driver:
            browser.quit()


def html_to_visible_text(html: str) -> str:
    """Extract readable text from HTML, dropping scripts and styles.

    Args:
        html: Rendered HTML from the browser (or a fixture).

    Returns:
        Normalized whitespace-separated visible text.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def build_summary_and_requirements(text: str) -> tuple[str, list[str]]:
    """Build a short summary and requirement-like lines from page text.

    Heuristic only (no LLM): summary is the first substantial paragraph;
    requirements are bullet-like or keyword-hinted lines. Prefer
    ``analyze_job_text_with_ollama`` for production summarization.

    Args:
        text: Visible text from ``html_to_visible_text``.

    Returns:
        ``(summary, requirements)`` where ``requirements`` is a non-empty
        list when extraction finds candidates; otherwise a single fallback
        item derived from the summary.

    Raises:
        ValueError: If ``text`` is empty or whitespace-only.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("page text is empty")

    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    summary_parts: list[str] = []
    for line in lines:
        if len(line) < 40:
            continue
        summary_parts.append(line)
        if sum(len(part) for part in summary_parts) >= 280:
            break
    summary = " ".join(summary_parts)[:500].strip() if summary_parts else lines[0][:500]

    requirements: list[str] = []
    seen: set[str] = set()
    for line in lines:
        normalized = _BULLET_PREFIX.sub("", line).strip()
        if len(normalized) < 20 or len(normalized) > 300:
            continue
        looks_like_bullet = bool(_BULLET_PREFIX.match(line))
        looks_like_requirement = bool(_REQUIREMENT_HINT.search(normalized))
        if not (looks_like_bullet or looks_like_requirement):
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        requirements.append(normalized)
        if len(requirements) >= 20:
            break

    if not requirements:
        requirements = [summary]

    return summary, requirements


def process_pending_jobs(
    conn,
    *,
    fetch_html: FetchHtml | None = None,
    analyze_text: AnalyzeFn | None = None,
    provider: str | None = None,
    ollama_client: httpx.Client | None = None,
) -> dict[str, list[str]]:
    """Scrape and analyze all ``pending`` / ``error`` jobs on the connection.

    Default analysis uses local Ollama into a Pydantic ``JobAnalysis``. Set
    ``JOB_ANALYSIS_PROVIDER=heuristic`` (or ``provider='heuristic'``) for the
    non-LLM path. One job failure does not stop the batch.

    Args:
        conn: Open SQLite connection from ``init_db``.
        fetch_html: Optional callable ``(url) -> html`` for tests. Defaults
            to ``fetch_rendered_html`` (Selenium).
        analyze_text: Optional ``(page_text) -> JobAnalysis`` override.
        provider: ``ollama`` or ``heuristic``; defaults to env.
        ollama_client: Optional shared ``httpx.Client`` for Ollama calls.

    Returns:
        Dict with ``ready`` and ``error`` lists of ``job_id`` values processed
        in this run.
    """
    fetch = fetch_html or fetch_rendered_html
    selected = (provider or job_analysis_provider()).strip().lower()

    if analyze_text is not None:
        analyzer: AnalyzeFn = analyze_text
    elif selected == "heuristic":
        analyzer = analyze_job_text_heuristic
    elif selected == "ollama":

        def analyzer(page_text: str) -> JobAnalysis:
            return analyze_job_text_with_ollama(page_text, client=ollama_client)

    else:
        raise ValueError(f"unsupported job analysis provider: {selected!r}")

    result: dict[str, list[str]] = {"ready": [], "error": []}

    for job_id, job_link in list_jobs_to_process(conn):
        try:
            html = fetch(job_link)
            text = html_to_visible_text(html)
            analysis = analyzer(text)
            mark_job_ready(conn, job_id, analysis.summary, analysis)
            result["ready"].append(job_id)
        except (WebDriverException, ValueError, OSError, RuntimeError, httpx.HTTPError) as exc:
            mark_job_error(conn, job_id, str(exc) or exc.__class__.__name__)
            result["error"].append(job_id)
        except Exception as exc:  # noqa: BLE001 — isolate unknown scrape failures
            mark_job_error(conn, job_id, str(exc) or exc.__class__.__name__)
            result["error"].append(job_id)

    return result
