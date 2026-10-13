# job-agent-lab

Local lab that turns Google Sheet job links into stored analyses and cover letters, then lets you polish a letter in chat or practice a typed phone screen, technical interview, or behavioral interview before you apply.

## End-to-end workflow

```text
Google Sheet
    │
    ▼
job-agent-lab run          ← batch (CLI)
    │  1. Sync all sheet rows into SQLite (metadata)
    │  2. For Status = Not applied only:
    │       scrape page (Selenium)
    │       → Ollama JobAnalysis (summary, requirements, skills)
    │       → first-pass cover letter (base docx + analysis)
    │
    ▼
data/jobs.db
    │
    ▼
adk web job_application_agents   ← interactive (Google ADK Web)
    │  pick apply_ollama / apply_claude / apply_qwen
    │  load job from DB + resume + base letter
    │  multi-turn polish → save final letter to DB
    │
    ▼
You apply on the employer site (manual)
```

Phone screen (separate chat, not part of the nightly run):

```text
adk web interview_agents
    upload resume PDF + fill assets/interview_brief.md
    select phone_screen, technical, or behavioral
    Bedrock Claude Haiku asks one question at a time
    debrief at the end (no job URL, no sheet write, no transcript saved)
```

**Applied** sheet rows are stored/updated as metadata only — no scrape, analysis, or letter.

## Quick start

```bash
uv sync --dev
cp .env.example .env   # fill Google + Ollama (and AWS if using Bedrock agents)
```

Replace placeholders:

- `assets/cover_letter.docx` — your base letter  
- `assets/resume.md` — your resume (`.md` / `.txt` / `.docx` for cover-letter chat; the phone screen also accepts `.pdf`, or set `RESUME_PATH`)
- `assets/interview_brief.md` — interview context: company name, job title, main job responsibilities, company culture, and business stream

### 1) Batch pipeline

Needs: `.env` Google vars, sheet shared with the service account, Chrome, Ollama (for analysis/letter).

```bash
uv run job-agent-lab run
uv run job-agent-lab list
uv run job-agent-lab show <job_id>
```

DB default: `data/jobs.db` (`--db` or `JOB_AGENT_DB_PATH`).

### 2) Interactive polish (ADK Web)

```bash
uv run adk web job_application_agents
```

Open the URL → select an agent:

| Agent | Backend |
|-------|---------|
| `apply_ollama` | Local Ollama |
| `apply_claude` | AWS Bedrock Claude |
| `apply_qwen` | AWS Bedrock Qwen |

Example prompts: list Not-applied jobs → load a `job_id` → revise the letter → save it to the DB.

### 3) Typed interviews (ADK Web)

Needs AWS credentials that can call Bedrock, and Claude Haiku 4.5 enabled in the account (`INTERVIEW_MODEL` overrides the default id).

```bash
uv run adk web interview_agents
```

Open the URL and select a chat:

| Chat | What it does |
|------|----------------|
| `phone_screen` | Recruiter-style screen: intro, resume walkthrough, culture, business stream |
| `technical` | Deeper design and debugging questions from the same resume and brief |
| `behavioral` | Real situations from the resume, shaped by the culture and the role |

Upload a resume PDF (or give a file path; `RESUME_PATH` is the fallback) and fill in [assets/interview_brief.md](assets/interview_brief.md) — company name, job title, main job responsibilities, company culture, and business stream. You can paste those sections in chat instead of editing the file. The interviewer asks one question per turn. On `technical`, a tool or domain that is in the brief but not on the resume is asked as a hypothetical, not as listed experience. Say you are done for a short debrief. These chats do not open a job URL, and they do not change the nightly pipeline or the sheet.

## Config (`.env`)

| Area | Vars |
|------|------|
| Sheets | `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_SHEET_ID` |
| Batch LLM | `OLLAMA_API_BASE`, `OLLAMA_MODEL`, `JOB_ANALYSIS_PROVIDER`, `COVER_LETTER_PROVIDER` |
| DB / assets | `JOB_AGENT_DB_PATH`, `RESUME_PATH`, `BASE_COVER_LETTER_PATH`, `INTERVIEW_BRIEF_PATH` |
| ADK models | `APPLY_MODEL_OLLAMA`, `APPLY_MODEL_CLAUDE`, `APPLY_MODEL_QWEN`, `INTERVIEW_MODEL`, `AWS_REGION` |

See `.env.example`. Never commit `.env` or credential JSON.

## Layout (mental model)

| Path | Role |
|------|------|
| `src/job_agent_lab/` | Library: sheets, scrape, analysis, cover letter, CLI, ADK tools |
| `job_application_agents/` | ADK Web cover-letter chats (`apply_ollama`, `apply_claude`, `apply_qwen`) |
| `interview_agents/` | ADK Web interviews (`phone_screen`, `technical`, `behavioral`) |
| `assets/` | Base cover letter, resume, and interview brief |
| `data/jobs.db` | Local SQLite (gitignored) |

## Tests / CI

```bash
uv run pytest
```

CI runs the same on PRs and pushes to `main`. Unit tests mock Google, browsers, and LLMs.

## Out of scope (for now)

Auto-apply, writing Status back to the sheet, cloud hosting, custom FastAPI UI (ADK Web is the chat UI; FastAPI inside ADK is not a separate app in this repo).
