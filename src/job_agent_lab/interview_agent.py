"""Google ADK phone-screen interviewer on AWS Bedrock Claude Haiku."""

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


def resolve_interview_model_id() -> str:
    """Return the LiteLLM model id for the phone-screen interviewer.

    Uses ``INTERVIEW_MODEL`` when set, otherwise Claude Haiku 4.5 on Bedrock.

    Returns:
        Model id string for ``LiteLlm``.
    """
    configured = os.environ.get("INTERVIEW_MODEL", "").strip()
    return configured or DEFAULT_INTERVIEW_MODEL


def build_interview_agent(*, name: str = "phone_screen") -> Agent:
    """Build the typed phone-screen agent.

    Args:
        name: ADK agent name shown in ``adk web``.

    Returns:
        Configured ``Agent`` that loads a resume and an interview brief, then
        asks one question at a time.
    """
    return Agent(
        model=LiteLlm(model=resolve_interview_model_id()),
        name=name,
        description=(
            "Runs a typed phone screen from an uploaded resume and a markdown "
            "interview brief using Bedrock Claude Haiku."
        ),
        instruction=INTERVIEW_INSTRUCTION,
        tools=[load_interview_resume, load_interview_brief],
    )


def build_interview_app() -> App:
    """Build the ADK app used by ``adk web interview_agents``.

    Uploaded files are stored as session artifacts. File bytes are not
    attached to the model request; the resume tool reads the artifact and
    returns plain text.

    Returns:
        ``App`` whose root agent is the phone-screen interviewer.
    """
    return App(
        name="phone_screen",
        root_agent=build_interview_agent(),
        plugins=[SaveFilesAsArtifactsPlugin(attach_file_reference=False)],
    )
