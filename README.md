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

## CI

Pull requests and pushes to `main` run the same test command on GitHub Actions (see `.github/workflows/ci.yml`).
