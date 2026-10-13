"""Phone-screen resume extraction, interview brief, and agent factory."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from google.genai import types

from job_agent_lab.interview_agent import (
    DEFAULT_INTERVIEW_MODEL,
    INTERVIEW_INSTRUCTION,
    BEHAVIORAL_INSTRUCTION,
    TECHNICAL_INSTRUCTION,
    build_interview_agent,
    build_interview_app,
    resolve_interview_model_id,
)
from job_agent_lab.interview_tools import (
    extract_pdf_text,
    load_interview_brief,
    load_interview_resume,
    parse_interview_brief,
)


def _minimal_pdf(text: str) -> bytes:
    """Build a one-page PDF whose extractable text is ``text``."""
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 24 Tf 72 720 Td ({safe}) Tj ET".encode("latin-1")
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        ),
        (
            b"4 0 obj\n<< /Length "
            + str(len(stream)).encode()
            + b" >>\nstream\n"
            + stream
            + b"\nendstream\nendobj\n"
        ),
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    header = b"%PDF-1.4\n"
    body = b""
    offsets: list[int] = []
    pos = len(header)
    for obj in objects:
        offsets.append(pos)
        body += obj
        pos += len(obj)
    xref = b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()
    trailer = (
        f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{pos}\n%%EOF\n".encode()
    )
    return header + body + xref + trailer


class _ArtifactContext:
    """Minimal stand-in for ADK ``ToolContext`` artifact methods."""

    def __init__(self, parts: dict[str, types.Part]) -> None:
        self._parts = parts

    async def list_artifacts(self) -> list[str]:
        return list(self._parts)

    async def load_artifact(self, filename: str, version: int | None = None):
        return self._parts.get(filename)


_FILLED_BRIEF = """
# Interview brief

## Company name

Northwind

## Job title

Data Engineer

## Main job responsibilities

Own batch pipelines and the warehouse models.

## Company culture

Small team, written decisions, direct feedback.

## Business stream

Retail analytics for store operations.
""".strip()


def test_extract_pdf_text_and_file_path(tmp_path: Path):
    pdf_bytes = _minimal_pdf("Python and SQL experience")
    assert "Python" in extract_pdf_text(pdf_bytes)

    pdf_path = tmp_path / "resume.pdf"
    pdf_path.write_bytes(pdf_bytes)
    loaded = asyncio.run(load_interview_resume(file_path=str(pdf_path)))
    assert loaded["success"] is True
    assert loaded["source"] == "file"
    assert "SQL" in loaded["resume_text"]


def test_load_resume_from_upload_and_env_fallback(tmp_path: Path, monkeypatch):
    pdf_bytes = _minimal_pdf("Uploaded warehouse experience")
    part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
    context = _ArtifactContext({"Sajan_resume.pdf": part})
    loaded = asyncio.run(load_interview_resume(tool_context=context))
    assert loaded["success"] is True
    assert loaded["source"] == "artifact"
    assert loaded["path"] == "Sajan_resume.pdf"
    assert "warehouse" in loaded["resume_text"]

    resume = tmp_path / "resume.md"
    resume.write_text("# Me\n\nSpark pipelines.\n", encoding="utf-8")
    monkeypatch.setenv("RESUME_PATH", str(resume))
    fallback = asyncio.run(load_interview_resume())
    assert fallback["success"] is True
    assert fallback["source"] == "resume_path"
    assert "Spark" in fallback["resume_text"]


def test_parse_and_load_filled_interview_brief(tmp_path, monkeypatch):
    parsed = parse_interview_brief(_FILLED_BRIEF)
    assert parsed["company_name"] == "Northwind"
    assert parsed["job_title"] == "Data Engineer"
    assert "warehouse" in parsed["main_job_responsibilities"]
    assert "direct feedback" in parsed["company_culture"]
    assert "Retail analytics" in parsed["business_stream"]

    brief = tmp_path / "brief.md"
    brief.write_text(_FILLED_BRIEF, encoding="utf-8")
    monkeypatch.setenv("INTERVIEW_BRIEF_PATH", str(brief))
    loaded = load_interview_brief()
    assert loaded["success"] is True
    assert loaded["source"] == "file"
    assert "store operations" in loaded["business_stream"]
    assert "store operations" in loaded["brief_text"]


def test_unfilled_brief_names_missing_sections(tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text(
        "\n".join(
            [
                "## Main job responsibilities",
                "",
                "(fill in)",
                "",
                "## Company culture",
                "",
                "Blameless reviews.",
                "",
                "## Business stream",
                "",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="main job responsibilities"):
        parse_interview_brief(brief.read_text(encoding="utf-8"))

    loaded = load_interview_brief(file_path=str(brief))
    assert loaded["success"] is False
    assert "company name" in loaded["missing"]
    assert "job title" in loaded["missing"]
    assert "main job responsibilities" in loaded["missing"]
    assert "business stream" in loaded["missing"]
    assert "company culture" not in loaded["missing"]


def test_chat_input_overrides_brief_file(tmp_path, monkeypatch):
    brief = tmp_path / "brief.md"
    brief.write_text(_FILLED_BRIEF, encoding="utf-8")
    monkeypatch.setenv("INTERVIEW_BRIEF_PATH", str(brief))
    loaded = load_interview_brief(
        business_stream="Payments infrastructure for online checkout."
    )
    assert loaded["success"] is True
    assert loaded["source"] == "file+input"
    assert "checkout" in loaded["business_stream"]
    assert "warehouse" in loaded["main_job_responsibilities"]


def test_chat_input_works_without_a_brief_file(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERVIEW_BRIEF_PATH", str(tmp_path / "missing.md"))
    loaded = load_interview_brief(
        company_name="Northwind",
        job_title="Analytics Engineer",
        main_job_responsibilities="Build the metrics layer.",
        company_culture="Written proposals before big changes.",
        business_stream="Subscription media.",
    )
    assert loaded["success"] is True
    assert loaded["source"] == "input"
    assert "metrics layer" in loaded["main_job_responsibilities"]


def test_build_interview_agent_uses_haiku_env(monkeypatch):
    monkeypatch.delenv("INTERVIEW_MODEL", raising=False)
    assert resolve_interview_model_id() == DEFAULT_INTERVIEW_MODEL
    assert "haiku" in DEFAULT_INTERVIEW_MODEL

    monkeypatch.setenv("INTERVIEW_MODEL", "bedrock/custom-haiku")
    agent = build_interview_agent()
    assert agent.name == "phone_screen"
    assert agent.model.model == "bedrock/custom-haiku"
    assert len(agent.tools) == 2
    assert "one question" in INTERVIEW_INSTRUCTION.lower()
    assert "Do not invent" in INTERVIEW_INSTRUCTION
    assert "load_interview_brief" in INTERVIEW_INSTRUCTION
    assert "load_job_posting" not in INTERVIEW_INSTRUCTION
    assert "business stream" in INTERVIEW_INSTRUCTION.lower()

    app = build_interview_app()
    assert app.name == "phone_screen"
    assert app.root_agent.name == "phone_screen"
    assert len(app.plugins) == 1
    assert type(app.plugins[0]).__name__ == "SaveFilesAsArtifactsPlugin"
    assert app.plugins[0]._attach_file_reference is False


def test_build_technical_interview(monkeypatch):
    monkeypatch.setenv("INTERVIEW_MODEL", "bedrock/custom-haiku")
    agent = build_interview_agent(kind="technical")
    assert agent.name == "technical"
    assert agent.model.model == "bedrock/custom-haiku"
    assert len(agent.tools) == 2
    assert "one question" in TECHNICAL_INSTRUCTION.lower()
    assert "Do not invent" in TECHNICAL_INSTRUCTION
    assert "not on the resume" in TECHNICAL_INSTRUCTION
    assert "load_interview_brief" in TECHNICAL_INSTRUCTION
    assert "load_job_posting" not in TECHNICAL_INSTRUCTION
    assert agent.instruction == TECHNICAL_INSTRUCTION

    app = build_interview_app(kind="technical")
    assert app.name == "technical"
    assert app.root_agent.name == "technical"
    assert type(app.plugins[0]).__name__ == "SaveFilesAsArtifactsPlugin"
    assert app.plugins[0]._attach_file_reference is False


def test_build_behavioral_interview(monkeypatch):
    monkeypatch.setenv("INTERVIEW_MODEL", "bedrock/custom-haiku")
    agent = build_interview_agent(kind="behavioral")
    assert agent.name == "behavioral"
    assert agent.model.model == "bedrock/custom-haiku"
    assert len(agent.tools) == 2
    assert "one question" in BEHAVIORAL_INSTRUCTION.lower()
    assert "Do not invent" in BEHAVIORAL_INSTRUCTION
    assert "real situation" in BEHAVIORAL_INSTRUCTION
    assert "not on the resume" in BEHAVIORAL_INSTRUCTION
    assert "load_interview_brief" in BEHAVIORAL_INSTRUCTION
    assert "load_job_posting" not in BEHAVIORAL_INSTRUCTION
    assert agent.instruction == BEHAVIORAL_INSTRUCTION

    app = build_interview_app(kind="behavioral")
    assert app.name == "behavioral"
    assert app.root_agent.name == "behavioral"
    assert type(app.plugins[0]).__name__ == "SaveFilesAsArtifactsPlugin"
    assert app.plugins[0]._attach_file_reference is False


def test_unknown_interview_kind_is_rejected():
    with pytest.raises(ValueError, match="phone_screen"):
        build_interview_agent(kind="panel")
