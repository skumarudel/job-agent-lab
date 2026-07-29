# job-agent-lab

Automate preparing job applications from links saved in a Google Sheet: scrape each posting, extract what the role requires, generate a tailored cover letter from a base letter, store results locally, and expose a CLI so you can apply the next day with everything ready.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync --dev
uv run pytest
```

## CLI

```bash
# Full pipeline: sheet sync → scrape/analyze → cover letters
uv run job-agent-lab run

# Inspect results
uv run job-agent-lab list
uv run job-agent-lab show <job_id>
```

Default DB path is `data/jobs.db` (created on first run). Override with `--db` or `JOB_AGENT_DB_PATH`. Loads `.env` automatically for Google/Ollama settings.

## Google Sheets (optional for local use)

Copy `.env.example` to `.env` and set:

- `GOOGLE_SERVICE_ACCOUNT_FILE` — path to the service account JSON
- `GOOGLE_SHEET_ID` — spreadsheet ID

Share the sheet with the service account email (Viewer). Unit tests mock the Sheets API and do not need credentials.

## Scraping (local live use)

Pending jobs are scraped with **headless Chrome via Selenium** (JavaScript-rendered boards). Page text is summarized with **local Ollama** into a Pydantic `JobAnalysis` (`summary`, `key_requirements`, `important_skills`, `role_family`). Set `JOB_ANALYSIS_PROVIDER=heuristic` for the non-LLM path. Unit tests mock fetch/Ollama and do not need a browser or Ollama server.

Sheet **Status**: all rows are synced into SQLite (insert/update metadata). Scrape, analysis, and cover letters run only for **Not applied** jobs; **Applied** jobs stay metadata-only.

## Cover letter

Base letter lives at `assets/cover_letter.docx` (replace with your own).

By default letters are rewritten with a **local Ollama** model (`COVER_LETTER_PROVIDER=ollama`). Set in `.env`:

- `OLLAMA_API_BASE=http://localhost:11434`
- `OLLAMA_MODEL=ollama_chat/gemma4:e4b-mlx`
- `JOB_ANALYSIS_PROVIDER=ollama`
- `COVER_LETTER_PROVIDER=ollama`

The cover-letter prompt uses the structured job analysis (requirements + important skills + role family) together with the base letter. Still no invented experience.

Use `COVER_LETTER_PROVIDER=heuristic` for the non-LLM template path. Unit tests mock Ollama HTTP and do not need a running server.

## CI

Pull requests and pushes to `main` run the same test command on GitHub Actions (see `.github/workflows/ci.yml`).
