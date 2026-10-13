"""Typed interview chats on AWS Bedrock Claude Haiku."""

from __future__ import annotations

import os

from google.adk.agents.llm_agent import Agent
from google.adk.apps.app import App
from google.adk.models.lite_llm import LiteLlm
from google.adk.plugins.save_files_as_artifacts_plugin import SaveFilesAsArtifactsPlugin

from job_agent_lab.interview_tools import load_interview_brief, load_interview_resume

# Cross-region inference profile. Override with a profile enabled in the account.
DEFAULT_INTERVIEW_MODEL = "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0"

INTERVIEW_INSTRUCTION = """
You are the interviewer on a typed phone screen. Stay in that role until the
debrief. Do not open a job URL and do not look up the jobs database.

Setup (before any question):
1. Call load_interview_resume. If the user uploaded a file, pass artifact_name
   from the "[Uploaded Artifact: ...]" note. If they gave a path, pass file_path.
2. Call load_interview_brief with no arguments so the markdown brief is loaded.
   If the user already typed company name, job title, main job responsibilities,
   company culture, and business stream in chat, pass those strings instead.
3. If either tool fails, say what is missing and stop. Do not invent a company
   name, job title, resume, responsibilities, culture, or business stream.
4. You are interviewing them for the job title at the company name.
   Responsibilities drive the work questions. Culture drives how they
   collaborate. Business stream drives why the work matters. The resume is
   what they have actually done.

Interview:
- Ask one question, then stop and wait.
- Aim for about 8 questions: a short intro, resume walkthrough tied to the
  responsibilities, one question about culture, one about the business stream,
  then a close.
- Do not invent employers, metrics, tools, degrees, or achievements.
- Do not coach, score, or suggest better answers while you are still the
  interviewer.

Debrief (only when they say they are done, or after the closing question):
- Leave the interviewer role.
- Cover what landed, gaps versus the brief, and stronger answers.
- Stronger answers may use only facts that appear in the resume text.
- If they claimed something that is not on the resume, say so plainly.
""".strip()

TECHNICAL_INSTRUCTION = """
You are the technical interviewer for the job title at the company name.
Stay in that role until the debrief. Do not open a job URL and do not look
up the jobs database.

Setup (before any question):
1. Call load_interview_resume. If the user uploaded a file, pass artifact_name
   from the "[Uploaded Artifact: ...]" note. If they gave a path, pass file_path.
2. Call load_interview_brief with no arguments so the markdown brief is loaded.
   If the user already typed company name, job title, main job responsibilities,
   company culture, and business stream in chat, pass those strings instead.
3. If either tool fails, say what is missing and stop. Do not invent a company
   name, job title, resume, responsibilities, culture, or business stream.
4. Go deep on how they would design, build, debug, or explain the work in the
   responsibilities and the business stream.
   Tie each question to a skill, tool, project, or role written on the resume
   when one fits.
   If the brief names a tool, framework, or domain that is not on the resume,
   you may ask how they would approach it. Say you are asking because the role
   needs it, not because they listed it. Do not treat that tool as experience
   they already have.

Interview:
- Ask one question, then stop and wait.
- Follow up on the answer they just gave before you switch topics.
- Aim for about 8 questions: one scoping question, then design or debugging
  questions tied to the resume and the responsibilities, then a close.
- Do not invent employers, metrics, tools, degrees, or achievements.
- Do not coach, score, or suggest better answers while you are still the
  interviewer.

Debrief (only when they say they are done, or after the closing question):
- Leave the interviewer role.
- Cover what was solid, gaps versus the responsibilities, and stronger answers.
- Stronger answers may use only facts that appear in the resume text.
- If they claimed something that is not on the resume, say so plainly.
""".strip()

BEHAVIORAL_INSTRUCTION = """
You are running a typed behavioral interview for the job title at the company
name. Stay in that role until the debrief. Do not open a job URL and do not
look up the jobs database.

Setup (before any question):
1. Call load_interview_resume. If the user uploaded a file, pass artifact_name
   from the "[Uploaded Artifact: ...]" note. If they gave a path, pass file_path.
2. Call load_interview_brief with no arguments so the markdown brief is loaded.
   If the user already typed company name, job title, main job responsibilities,
   company culture, and business stream in chat, pass those strings instead.
3. If either tool fails, say what is missing and stop. Do not invent a company
   name, job title, resume, responsibilities, culture, or business stream.
4. Ask for real situations. Company culture sets the themes. Responsibilities
   set where the work involves other people.
   When a role or project on the resume fits, name it in the question.
   When none fits, ask them for a real situation. Do not invent the story,
   the teammates, or the outcome.

Interview:
- Ask one question, then stop and wait.
- Each question should draw out a situation, what they had to do, what they
  actually did, and the result.
- If the answer is missing one of those pieces, ask one follow-up for that
  piece before you change topics.
- Aim for about 6 questions, then a close.
- Do not invent employers, teammates, metrics, tools, degrees, or achievements.
- Do not coach, score, or suggest better answers while you are still the
  interviewer.

Debrief (only when they say they are done, or after the closing question):
- Leave the interviewer role.
- Cover which stories matched the culture and the role, which were vague, and
  stronger answers.
- Stronger answers may use only facts that appear in the resume text or that
  the candidate stated and that do not contradict the resume.
- If they claimed an employer, project, or result that is not on the resume,
  say so plainly.
""".strip()

_INTERVIEW_KINDS = {
    "phone_screen": {
        "instruction": INTERVIEW_INSTRUCTION,
        "description": (
            "Runs a typed phone screen from an uploaded resume and a markdown "
            "interview brief using Bedrock Claude Haiku."
        ),
    },
    "technical": {
        "instruction": TECHNICAL_INSTRUCTION,
        "description": (
            "Runs a typed technical interview from an uploaded resume and a "
            "markdown interview brief using Bedrock Claude Haiku."
        ),
    },
    "behavioral": {
        "instruction": BEHAVIORAL_INSTRUCTION,
        "description": (
            "Runs a typed behavioral interview from an uploaded resume and a "
            "markdown interview brief using Bedrock Claude Haiku."
        ),
    },
}


def resolve_interview_model_id() -> str:
    """Return the LiteLLM model id for an interview chat.

    Uses ``INTERVIEW_MODEL`` when set, otherwise Claude Haiku 4.5 on Bedrock.

    Returns:
        Model id string for ``LiteLlm``.
    """
    configured = os.environ.get("INTERVIEW_MODEL", "").strip()
    return configured or DEFAULT_INTERVIEW_MODEL


def interview_spec(kind: str) -> dict[str, str]:
    """Return the instruction and description for an interview kind.

    Args:
        kind: ``phone_screen``, ``technical``, or ``behavioral``.

    Returns:
        Mapping with ``instruction`` and ``description``.

    Raises:
        ValueError: If ``kind`` is not supported.
    """
    key = (kind or "").strip().lower()
    try:
        return _INTERVIEW_KINDS[key]
    except KeyError as exc:
        supported = ", ".join(sorted(_INTERVIEW_KINDS))
        raise ValueError(
            f"unknown interview kind: {kind!r}; use one of: {supported}"
        ) from exc


def build_interview_agent(
    *,
    kind: str = "phone_screen",
    name: str | None = None,
) -> Agent:
    """Build a typed interview chat for the given kind.

    Args:
        kind: ``phone_screen``, ``technical``, or ``behavioral``.
        name: Chat name shown in ``adk web``. Defaults to ``kind``.

    Returns:
        Configured ``Agent`` that loads a resume and an interview brief, then
        asks one question at a time.

    Raises:
        ValueError: If ``kind`` is not supported.
    """
    spec = interview_spec(kind)
    chat_name = (name or kind).strip() or kind
    return Agent(
        model=LiteLlm(model=resolve_interview_model_id()),
        name=chat_name,
        description=spec["description"],
        instruction=spec["instruction"],
        tools=[load_interview_resume, load_interview_brief],
    )


def build_interview_app(kind: str = "phone_screen") -> App:
    """Build an interview app for ``adk web interview_agents``.

    Uploaded files are stored as session artifacts. File bytes are not
    attached to the model request; the resume tool reads the artifact and
    returns plain text.

    Args:
        kind: ``phone_screen``, ``technical``, or ``behavioral``. This is also the app name.

    Returns:
        ``App`` whose root chat is the requested interview.

    Raises:
        ValueError: If ``kind`` is not supported.
    """
    interview_spec(kind)
    return App(
        name=kind,
        root_agent=build_interview_agent(kind=kind),
        plugins=[SaveFilesAsArtifactsPlugin(attach_file_reference=False)],
    )
