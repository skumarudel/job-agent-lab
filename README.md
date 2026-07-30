# job-agent-lab

Local lab that turns Google Sheet job links into stored analyses and cover letters, then lets you polish a letter in chat before you apply.

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
adk web apply_agents       ← interactive (Google ADK Web)
    │  pick apply_ollama / apply_claude / apply_qwen
    │  load job from DB + resume + base letter
    │  multi-turn polish → save final letter to DB
    │
    ▼
You apply on the employer site (manual)
```

**Applied** sheet rows are stored/updated as metadata only — no scrape, analysis, or letter.

## Quick start

```bash
uv sync --dev
cp .env.example .env   # fill Google + Ollama (and AWS if using Bedrock agents)
```

Replace placeholders:

- `assets/cover_letter.docx` — your base letter  
- `assets/resume.md` — your resume (`.md` / `.txt` / `.docx`; or set `RESUME_PATH`)

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
uv run adk web apply_agents
```

Open the URL → select an agent:

| Agent | Backend |
|-------|---------|
| `apply_ollama` | Local Ollama |
| `apply_claude` | AWS Bedrock Claude |
| `apply_qwen` | AWS Bedrock Qwen |

Example prompts: list Not-applied jobs → load a `job_id` → revise the letter → save it to the DB.

## Config (`.env`)

| Area | Vars |
|------|------|
| Sheets | `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_SHEET_ID` |
| Batch LLM | `OLLAMA_API_BASE`, `OLLAMA_MODEL`, `JOB_ANALYSIS_PROVIDER`, `COVER_LETTER_PROVIDER` |
| DB / assets | `JOB_AGENT_DB_PATH`, `RESUME_PATH`, `BASE_COVER_LETTER_PATH` |
| ADK models | `APPLY_MODEL_OLLAMA`, `APPLY_MODEL_CLAUDE`, `APPLY_MODEL_QWEN`, `AWS_REGION` |

See `.env.example`. Never commit `.env` or credential JSON.

## Layout (mental model)

| Path | Role |
|------|------|
| `src/job_agent_lab/` | Library: sheets, scrape, analysis, cover letter, CLI, ADK tools |
| `apply_agents/` | ADK Web entrypoints (`apply_ollama`, …) |
| `assets/` | Base cover letter + resume |
| `data/jobs.db` | Local SQLite (gitignored) |

## Tests / CI

```bash
uv run pytest
```

CI runs the same on PRs and pushes to `main`. Unit tests mock Google, browsers, and LLMs.

## Out of scope (for now)

Auto-apply, writing Status back to the sheet, cloud hosting, custom FastAPI UI (ADK Web is the chat UI; FastAPI inside ADK is not a separate app in this repo).
