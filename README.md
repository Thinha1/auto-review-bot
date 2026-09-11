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

For the complete GitHub-to-Discord flow, also configure the GitHub App, GitHub OAuth,
and OpenAI variables documented in `.env.example`.

## GitHub configuration

Create a GitHub App with these repository permissions:

- Contents: read
- Pull requests: read
- Metadata: read

To publish review results in the PR Checks tab, also grant `Checks: write` and set
`GITHUB_CHECKS_ENABLED=true`. This is opt-in so existing installations without the extra
permission continue to work. Reviews with high or critical findings conclude with
`action_required`; lower-severity findings are `neutral`, and an empty review is `success`.

Subscribe to `pull_request`, `installation`, and `installation_repositories`. Configure:

- Webhook URL: `https://YOUR_HOST/api/webhooks/github`
- Callback URL: `https://YOUR_HOST/auth/github/callback`
- Webhook secret: the same value as `GITHUB_WEBHOOK_SECRET`
- `GITHUB_APP_SLUG`: the app slug used by the dashboard's installation link

The dashboard uses GitHub OAuth and maps each repository's effective GitHub permissions to
application roles. This works for personal repositories and organization access inherited from
teams, base permissions, or organization ownership:

- `viewer` (GitHub read/triage): view repository status and review history.
- `maintainer` (GitHub write/maintain): viewer access plus retry and Discord resend operations.
- `admin` (GitHub admin): maintainer access plus review-policy and secret configuration.

Authorization is checked against GitHub on every dashboard request; webhook account metadata is
never treated as proof of a user's organization role.

## Run the API

```bash
uv run uvicorn app.main:app --reload
```

Health check:

```bash
curl http://127.0.0.1:8000/healthz
# {"status":"ok"}
```

Apply migrations and run the worker in a separate process:

```bash
uv run alembic upgrade head
uv run python -m app.worker
```

Open `http://127.0.0.1:8000/dashboard` to sign in and configure repositories.

## Review a local fixture

Use a real model:

```bash
uv run python -m app.cli tests/fixtures/sample.diff --model gpt-5-mini
```

For an offline deterministic run, supply a file containing a valid model-output JSON:

```bash
uv run python -m app.cli tests/fixtures/sample.diff --fake-output review-output.json
```

## Development

| Task | Command |
| --- | --- |
| Install dependencies | `uv sync` |
| Run API (dev) | `uv run uvicorn app.main:app --reload` |
| Run worker | `uv run python -m app.worker` |
| Lint | `uv run ruff check .` |
| Format | `uv run ruff format .` |
| Type check | `uv run pyright` |
| Run tests | `uv run pytest` |
| Apply migrations | `uv run alembic upgrade head` |
| New migration (autogenerate) | `uv run alembic revision --autogenerate -m "message"` |
| Current revision | `uv run alembic current` |

Migrations read `DATABASE_URL` (defaults to `sqlite:///./auto_review.db`).
Production deployments can use `postgresql+psycopg://...`; PostgreSQL workers claim jobs with
`FOR UPDATE SKIP LOCKED` so replicas do not block one another.

## Usage budgets

Each repository has a UTC calendar-month token budget, configured from its dashboard settings.
Before any model call, the worker atomically reserves a deterministic estimate made from the
rendered prompts plus `max_output_tokens_per_call` for every chunk. Competing workers therefore
cannot reserve the same remaining capacity. Once a review finishes, the reservation is replaced
with the provider's actual input/output usage and model-call count; actual usage may be higher
than the estimate, in which case remaining capacity is reported as zero.

Runs that cannot reserve their full estimate are marked `skipped` with
`monthly_token_budget_exceeded` before contacting the model. The dashboard shows durable used,
reserved, remaining-token, and model-call counters from the database; these survive process
restarts, unlike the process-local `/metrics` counters.

## Operations

- `/healthz` reports API process health.
- `/readyz` verifies the database is reachable.
- `/metrics` exposes process-local Prometheus text metrics.
- Monthly repository usage and outstanding reservations are persisted in the database and shown
  in the dashboard.
- GitHub Check publication is recorded separately from the review run. A Checks API failure
  does not discard the review result or prevent its Discord delivery.
- SQLite uses WAL and a five-second busy timeout. Keep the database on a local persistent
  volume, not a network filesystem.
- Review jobs use renewable leases. A crashed worker's job becomes eligible after the lease
  expires; model and notification errors never persist raw exception text.
- See [operations.md](./docs/operations.md) for migrations, key rotation, and stuck-job
  recovery.
- See [mvp-acceptance.md](./docs/mvp-acceptance.md) for automated evidence and pre-release
  checks.

## Docker

```bash
docker compose up --build
```

The Compose stack shares `/data/auto_review.db` between API, migration, and worker services.

### Pre-commit

```bash
uvx pre-commit install         # install the git hook
uvx pre-commit run --all-files # run all hooks once, now
```

### CI

`.github/workflows/ci.yml` runs Ruff (format + lint), Pyright, an empty-database
migration, and the test suite on every push and pull request.
