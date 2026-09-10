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

## Development

| Task | Command |
| --- | --- |
| Install dependencies | `uv sync` |
| Run API (dev) | `uv run uvicorn app.main:app --reload` |
| Lint | `uv run ruff check .` |
| Format | `uv run ruff format .` |
| Type check | `uv run pyright` |
| Run tests | `uv run pytest` |
| Apply migrations | `uv run alembic upgrade head` |
| New migration (autogenerate) | `uv run alembic revision --autogenerate -m "message"` |
| Current revision | `uv run alembic current` |

Migrations read `DATABASE_URL` (defaults to `sqlite:///./auto_review.db`).

### Pre-commit

```bash
uvx pre-commit install         # install the git hook
uvx pre-commit run --all-files # run all hooks once, now
```

### CI

`.github/workflows/ci.yml` runs Ruff (format + lint), Pyright, an empty-database
migration, and the test suite on every push and pull request.
