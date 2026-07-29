# job-agent-lab

Automate preparing job applications from links saved in a Google Sheet: scrape each posting, extract what the role requires, generate a tailored cover letter from a base letter, store results locally, and expose a CLI so you can apply the next day with everything ready.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync --dev
uv run pytest
```

## Google Sheets (optional for local use)

Copy `.env.example` to `.env` and set:

- `GOOGLE_SERVICE_ACCOUNT_FILE` — path to the service account JSON
- `GOOGLE_SHEET_ID` — spreadsheet ID

Share the sheet with the service account email (Viewer). Unit tests mock the Sheets API and do not need credentials.

## Scraping (local live use)

Pending jobs are scraped with **headless Chrome via Selenium** (JavaScript-rendered boards). Unit tests mock the fetch and do not need a browser. For a real scrape you need Chrome installed; Selenium Manager resolves the driver.

## Cover letter

Base letter lives at `assets/cover_letter.docx` (replace with your own).

By default letters are rewritten with a **local Ollama** model (`COVER_LETTER_PROVIDER=ollama`). Set in `.env`:

- `OLLAMA_API_BASE=http://localhost:11434`
- `OLLAMA_MODEL=ollama_chat/gemma4:e4b-mlx`

The rewrite prompt steers toward the lab's three target role families: **Data Engineer**, **Analytics Engineer**, and **Data Scientist** (closest match from the posting; still no invented experience).

Use `COVER_LETTER_PROVIDER=heuristic` for the non-LLM template path. Unit tests mock Ollama HTTP and do not need a running server.

## CI

Pull requests and pushes to `main` run the same test command on GitHub Actions (see `.github/workflows/ci.yml`).
