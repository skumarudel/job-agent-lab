# job-agent-lab

Automate preparing job applications from links saved in a Google Sheet: scrape each posting, extract what the role requires, generate a tailored cover letter from a base letter, store results locally, and expose a CLI so you can apply the next day with everything ready.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync --dev
uv run pytest
```

## CI

Pull requests and pushes to `main` run the same test command on GitHub Actions (see `.github/workflows/ci.yml`).
