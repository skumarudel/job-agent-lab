"""Shared Google ADK apply-agent factory for Ollama / Claude / Qwen variants."""

from __future__ import annotations

import os

from google.adk.agents.llm_agent import Agent
from google.adk.models.lite_llm import LiteLlm

from job_agent_lab.apply_tools import (
    get_job_for_apply,
    list_jobs_for_apply,
    load_base_cover_letter_tool,
    load_resume_text,
    save_cover_letter_to_db,
)

DEFAULT_OLLAMA_MODEL = "ollama_chat/gemma4:e4b-mlx"
# LiteLLM Bedrock ids — override via env with models enabled in your account.
DEFAULT_CLAUDE_MODEL = "bedrock/anthropic.claude-sonnet-4-20250514-v1:0"
DEFAULT_QWEN_MODEL = "bedrock/qwen.qwen3-235b-a22b-2507"


APPLY_INSTRUCTION = """
You help the candidate polish a cover letter for a job already stored in the
local SQLite database (populated by job-agent-lab run).

Workflow:
1. Call list_jobs_for_apply to show Not-applied jobs (or ask the user for a job_id).
2. Call get_job_for_apply with the chosen job_id.
3. Call load_base_cover_letter_tool and load_resume_text.
4. Brainstorm with the user over multiple turns. After each meaningful revision,
   show the full current draft cover letter in your reply.
5. When the user is satisfied, call save_cover_letter_to_db with job_id and the
   final cover_letter text.

Strict rules:
- Use ONLY experience, skills, projects, and claims present in the resume and
  the base cover letter.
- Do NOT invent employers, metrics, tools, degrees, or achievements.
- Use the job analysis (summary, key requirements, important skills, role_family)
  only to choose emphasis and wording.
- Target Data Engineer, Analytics Engineer, or Data Scientist language when it
  matches the job's role_family and the resume supports it.
- Keep the letter concise and professional.
- Prefer cutting over inventing when something does not fit.
""".strip()


def resolve_model_id(variant: str) -> str:
    """Return the LiteLLM model id for an apply-agent variant.

    Args:
        variant: One of ``ollama``, ``claude``, ``qwen``.

    Returns:
        Model id string for ``LiteLlm``.
    """
    key = (variant or "").strip().lower()
    if key == "ollama":
        return os.environ.get("APPLY_MODEL_OLLAMA", DEFAULT_OLLAMA_MODEL).strip()
    if key == "claude":
        return os.environ.get("APPLY_MODEL_CLAUDE", DEFAULT_CLAUDE_MODEL).strip()
    if key == "qwen":
        return os.environ.get("APPLY_MODEL_QWEN", DEFAULT_QWEN_MODEL).strip()
    raise ValueError(f"unknown apply agent variant: {variant!r}")


def build_apply_agent(*, variant: str, name: str) -> Agent:
    """Build an ADK apply agent for the given model variant.

    Args:
        variant: ``ollama``, ``claude``, or ``qwen``.
        name: ADK agent name shown in ``adk web``.

    Returns:
        Configured ``Agent`` instance.
    """
    model_id = resolve_model_id(variant)
    return Agent(
        model=LiteLlm(model=model_id),
        name=name,
        description=(
            "Polishes cover letters for jobs in the local SQLite DB using the "
            f"{variant} model path, resume, and base letter (multi-turn chat)."
        ),
        instruction=APPLY_INSTRUCTION,
        tools=[
            list_jobs_for_apply,
            get_job_for_apply,
            load_base_cover_letter_tool,
            load_resume_text,
            save_cover_letter_to_db,
        ],
    )
