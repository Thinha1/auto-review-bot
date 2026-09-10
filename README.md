# PR Review Agent (auto-review-discord)

Reviews GitHub pull requests with an AI and posts structured findings to a
per-repository Discord webhook. Includes a dashboard to manage multiple
repositories and review history.

The service only ever comments — it never merges, approves, or rejects a PR.

- Design: [pr-review-agent-design.md](./pr-review-agent-design.md)
- MVP plan: [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md)

## Prerequisites

- [uv](https://docs.astral.sh/uv/)
- Python 3.12 or newer (the project pins `.python-version` to 3.13)

## Setup

```bash
uv sync
cp .env.example .env
```

Then fill in the required secrets in `.env` (never commit `.env`):

- `GITHUB_WEBHOOK_SECRET` — shared secret for verifying `X-Hub-Signature-256`.
- `MASTER_KEY` — key used to encrypt stored Discord webhook URLs (32+ random bytes).

## Run the API

```bash
uv run uvicorn app.main:app --reload
```

Health check:

```bash
curl http://127.0.0.1:8000/healthz
# {"status":"ok"}
```
