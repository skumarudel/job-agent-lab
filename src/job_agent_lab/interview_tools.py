"""ADK tools for a typed phone screen: resume text and an interview brief."""

from __future__ import annotations

import os
import re
from io import BytesIO
from pathlib import Path

from docx import Document
from google.adk.tools.tool_context import ToolContext
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from job_agent_lab.apply_tools import resolve_resume_path

RESUME_TEXT_LIMIT = 20_000
BRIEF_TEXT_LIMIT = 12_000
DEFAULT_INTERVIEW_BRIEF_PATH = Path("assets/interview_brief.md")
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_TEXT_SUFFIXES = {".md", ".txt", ".text"}
_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_FILL_IN = re.compile(r"(?i)^\(fill in\)$")
_BRIEF_FIELDS = (
    ("company_name", "company name"),
    ("job_title", "job title"),
    ("main_job_responsibilities", "main job responsibilities"),
    ("company_culture", "company culture"),
    ("business_stream", "business stream"),
)


def extract_pdf_text(data: bytes) -> str:
    """Extract plain text from PDF bytes.

    Args:
        data: Raw PDF file contents.

    Returns:
        Non-empty text joined across pages.

    Raises:
        ValueError: If ``data`` is empty, unreadable, or has no text.
    """
    if not data:
        raise ValueError("PDF is empty")
    try:
        reader = PdfReader(BytesIO(data))
        chunks = [page.extract_text() or "" for page in reader.pages]
    except PdfReadError as exc:
        raise ValueError(f"Could not read PDF: {exc}") from exc
    text = "\n".join(chunk.strip() for chunk in chunks if chunk.strip()).strip()
    if not text:
        raise ValueError("PDF has no extractable text")
    return text


def text_from_resume_bytes(
    data: bytes,
    *,
    mime_type: str = "",
    filename: str = "",
) -> str:
    """Turn resume bytes into plain text.

    Args:
        data: File contents.
        mime_type: MIME type when known (for example ``application/pdf``).
        filename: Original name, used for the suffix when MIME is missing.

    Returns:
        Non-empty resume text.

    Raises:
        ValueError: If the format is unsupported or the content is empty.
    """
    suffix = Path(filename).suffix.lower()
    mime = (mime_type or "").split(";", 1)[0].strip().lower()
    if mime == "application/pdf" or suffix == ".pdf":
        return extract_pdf_text(data)
    if mime == _DOCX_MIME or suffix == ".docx":
        return _docx_text(BytesIO(data))
    if mime.startswith("text/") or suffix in _TEXT_SUFFIXES:
        text = data.decode("utf-8").strip()
        if not text:
            raise ValueError("Resume file is empty")
        return text
    label = suffix or mime or "unknown"
    raise ValueError(
        f"Unsupported resume format {label}; use .pdf, .md, .txt, or .docx"
    )


def extract_resume_file(path: str | Path) -> str:
    """Read resume plain text from a PDF, Markdown, text, or docx file.

    Args:
        path: Filesystem path to the resume.

    Returns:
        Non-empty plain text.

    Raises:
        FileNotFoundError: If ``path`` is not a file.
        ValueError: If the format is unsupported or the file has no text.
        OSError: If the file cannot be read.
    """
    resume_path = Path(path)
    if not resume_path.is_file():
        raise FileNotFoundError(f"Resume not found at {resume_path}")
    data = resume_path.read_bytes()
    mime = "application/pdf" if resume_path.suffix.lower() == ".pdf" else ""
    return text_from_resume_bytes(data, mime_type=mime, filename=resume_path.name)


async def load_interview_resume(
    artifact_name: str = "",
    file_path: str = "",
    tool_context: ToolContext | None = None,
) -> dict:
    """Load resume plain text for the phone screen.

    Checks an explicit file path, then an uploaded chat artifact, then
    ``RESUME_PATH`` (or ``assets/resume.md``). PDF bytes are converted to
    text locally and are not sent to the model as a file.

    Args:
        artifact_name: Uploaded artifact filename from the
            "[Uploaded Artifact: ...]" note. Leave empty to use a PDF
            upload when one is present.
        file_path: Optional filesystem path. Wins over uploads and
            ``RESUME_PATH``.

    Returns:
        Dict with a success flag and ``resume_text`` when loading works.
    """
    explicit_path = (file_path or "").strip()
    if explicit_path:
        return _resume_result_from_path(Path(explicit_path), source="file")

    artifact = (artifact_name or "").strip()
    if artifact and tool_context is None:
        return {
            "success": False,
            "error": "No chat session is available to load that uploaded resume.",
        }
    if tool_context is not None:
        loaded = await _resume_from_artifact(tool_context, artifact)
        if loaded is not None:
            return loaded

    return _resume_result_from_path(resolve_resume_path(), source="resume_path")


def resolve_interview_brief_path() -> Path:
    """Resolve the interview brief from ``INTERVIEW_BRIEF_PATH`` or the asset.

    Returns:
        Path to the markdown brief the candidate fills in.
    """
    env_path = os.environ.get("INTERVIEW_BRIEF_PATH", "").strip()
    return Path(env_path) if env_path else DEFAULT_INTERVIEW_BRIEF_PATH


def parse_interview_brief(markdown: str) -> dict[str, str]:
    """Pull the three required sections out of an interview brief.

    Required headings are ``Company name``, ``Job title``,
    ``Main job responsibilities``, ``Company culture``, and
    ``Business stream``. A section that is empty or still ``(fill in)``
    counts as missing.

    Args:
        markdown: Brief text, usually from ``assets/interview_brief.md``.

    Returns:
        Mapping of field name to the section body.

    Raises:
        ValueError: If a required heading is missing or still unfilled.
    """
    parsed = _filled_sections(markdown)
    missing = [heading for key, heading in _BRIEF_FIELDS if key not in parsed]
    if missing:
        names = ", ".join(missing)
        raise ValueError(
            "Interview brief is missing: "
            f"{names}. Fill those sections in the markdown brief."
        )
    return parsed


def load_interview_brief(
    company_name: str = "",
    job_title: str = "",
    main_job_responsibilities: str = "",
    company_culture: str = "",
    business_stream: str = "",
    file_path: str = "",
) -> dict:
    """Load the role context for the phone screen.

    Reads the markdown brief (``file_path``, ``INTERVIEW_BRIEF_PATH``, or
    ``assets/interview_brief.md``). Non-empty arguments override the matching
    section, so values typed in chat replace the file. Does not open a job
    URL or the jobs database.

    Args:
        company_name: Employer name typed in this turn.
        job_title: Role title the candidate is applying for.
        main_job_responsibilities: Responsibilities typed in this turn.
        company_culture: Culture notes typed in this turn.
        business_stream: Business-stream notes typed in this turn.
        file_path: Optional brief path. Empty uses the configured default.

    Returns:
        Dict with a success flag and the five section texts.
    """
    overrides = {
        "company_name": (company_name or "").strip(),
        "job_title": (job_title or "").strip(),
        "main_job_responsibilities": (main_job_responsibilities or "").strip(),
        "company_culture": (company_culture or "").strip(),
        "business_stream": (business_stream or "").strip(),
    }
    path = Path(file_path.strip()) if file_path and file_path.strip() else None
    from_file: dict[str, str] = {}
    used_path = ""
    if path is not None or not all(overrides.values()):
        brief_path = path or resolve_interview_brief_path()
        used_path = str(brief_path)
        try:
            markdown = brief_path.read_text(encoding="utf-8")
        except OSError as exc:
            if not all(overrides.values()):
                return {
                    "success": False,
                    "error": (
                        f"Interview brief not found at {brief_path}. "
                        "Fill assets/interview_brief.md or pass every brief section."
                    ),
                    "path": str(brief_path),
                    "detail": str(exc),
                }
        else:
            from_file = _filled_sections(markdown)

    merged = dict(from_file)
    for key, value in overrides.items():
        if value and not _section_is_blank(value):
            merged[key] = value.strip()

    missing = [heading for key, heading in _BRIEF_FIELDS if key not in merged]
    if missing:
        return {
            "success": False,
            "error": (
                "Interview brief is missing: "
                + ", ".join(missing)
                + ". Fill those sections in the markdown brief or paste them."
            ),
            "path": used_path,
            "missing": missing,
        }

    typed = any(value and not _section_is_blank(value) for value in overrides.values())
    if typed and from_file:
        source = "file+input"
    elif typed:
        source = "input"
    else:
        source = "file"
    brief_text = "\n\n".join(
        f"## {heading}\n\n{merged[key]}" for key, heading in _BRIEF_FIELDS
    )
    clipped, truncated = _truncate(brief_text, BRIEF_TEXT_LIMIT)
    return {
        "success": True,
        "source": source,
        "path": used_path,
        "company_name": merged["company_name"],
        "job_title": merged["job_title"],
        "main_job_responsibilities": merged["main_job_responsibilities"],
        "company_culture": merged["company_culture"],
        "business_stream": merged["business_stream"],
        "brief_text": clipped,
        "truncated": truncated,
    }


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """Trim text to ``limit`` characters and flag when that happened."""
    cleaned = (text or "").strip()
    if len(cleaned) <= limit:
        return cleaned, False
    return cleaned[:limit].rstrip() + "\n...[truncated]...", True


def _docx_text(source: BytesIO | Path) -> str:
    """Join non-empty paragraphs from a docx file or buffer."""
    document = Document(source)
    paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    text = "\n\n".join(paragraphs).strip()
    if not text:
        raise ValueError("Resume file is empty")
    return text


def _resume_payload(text: str, *, source: str, label: str) -> dict:
    """Build a successful resume tool response, truncating long text."""
    clipped, truncated = _truncate(text, RESUME_TEXT_LIMIT)
    return {
        "success": True,
        "source": source,
        "path": label,
        "resume_text": clipped,
        "truncated": truncated,
    }


def _resume_result_from_path(path: Path, *, source: str) -> dict:
    """Load a resume file into a tool response dict."""
    try:
        text = extract_resume_file(path)
    except FileNotFoundError:
        return {
            "success": False,
            "error": (
                f"Resume not found at {path}. Upload a PDF, pass a file path, "
                "or set RESUME_PATH."
            ),
            "path": str(path),
        }
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return {"success": False, "error": str(exc), "path": str(path)}
    return _resume_payload(text, source=source, label=str(path))


def _bytes_and_mime(part: object) -> tuple[bytes, str]:
    """Read bytes and MIME type from an ADK artifact part."""
    inline = getattr(part, "inline_data", None)
    if inline is not None and getattr(inline, "data", None):
        data = inline.data
        if isinstance(data, str):
            data = data.encode("utf-8")
        mime = (getattr(inline, "mime_type", None) or "").split(";", 1)[0].strip()
        return bytes(data), mime.lower()
    text = getattr(part, "text", None)
    if isinstance(text, str) and text.strip():
        return text.encode("utf-8"), "text/plain"
    raise ValueError("Uploaded resume artifact has no readable content")


async def _pick_artifact_name(tool_context: ToolContext, requested: str) -> str:
    """Choose an artifact filename, preferring an explicit name or a PDF."""
    if requested:
        return requested
    try:
        names = list(await tool_context.list_artifacts())
    except (ValueError, AttributeError):
        return ""
    for name in names:
        if Path(name).suffix.lower() == ".pdf":
            return name
    return names[0] if names else ""


async def _resume_from_artifact(
    tool_context: ToolContext,
    artifact_name: str,
) -> dict | None:
    """Load resume text from a session artifact, or None if none is selected."""
    name = await _pick_artifact_name(tool_context, artifact_name)
    if not name:
        return None
    try:
        part = await tool_context.load_artifact(name)
    except (ValueError, AttributeError) as exc:
        return {"success": False, "error": str(exc), "path": name}
    if part is None:
        if artifact_name:
            return {
                "success": False,
                "error": f"Uploaded resume artifact not found: {name}",
                "path": name,
            }
        return None
    try:
        data, mime = _bytes_and_mime(part)
        text = text_from_resume_bytes(data, mime_type=mime, filename=name)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return {"success": False, "error": str(exc), "path": name}
    return _resume_payload(text, source="artifact", label=name)


def _filled_sections(markdown: str) -> dict[str, str]:
    """Return required brief sections that have real text."""
    sections = _brief_sections(markdown or "")
    filled: dict[str, str] = {}
    for key, heading in _BRIEF_FIELDS:
        body = sections.get(heading, "")
        if not _section_is_blank(body):
            filled[key] = body.strip()
    return filled


def _normalize_heading(heading: str) -> str:
    """Lowercase a markdown heading and collapse internal whitespace."""
    return re.sub(r"\s+", " ", (heading or "").strip().lower())


def _brief_sections(markdown: str) -> dict[str, str]:
    """Map normalized ``##`` headings to the text under each heading."""
    matches = list(_HEADING.finditer(markdown))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        key = _normalize_heading(match.group(1))
        if key not in sections:
            sections[key] = markdown[start:end].strip()
    return sections


def _section_is_blank(body: str) -> bool:
    """Return True when a section has no text beyond the fill-in placeholder."""
    lines = [line.strip() for line in (body or "").splitlines()]
    content = [line for line in lines if line and not line.startswith("<!--")]
    if not content:
        return True
    return all(_FILL_IN.match(line) for line in content)
