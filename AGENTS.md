# AGENTS.md

## Status

The end-to-end MVP is implemented. Read `pr-review-agent-design.md` for product intent,
`IMPLEMENTATION_PLAN.md` for acceptance criteria, and `docs/operations.md` for operational
changes. Keep these documents aligned when behavior or guardrails change.

## What it builds

A "PR Review Agent": a GitHub App receives `pull_request` webhooks, queues a review run, an AI worker reviews the diff, and posts structured findings to a per-repo Discord webhook. A dashboard manages multiple repositories. It never merges/approves/rejects — comments only.

## Stack and verification

- Python **3.12+**, FastAPI, Pydantic, SQLAlchemy + Alembic, HTTPX, Jinja (dashboard)
- SQLite for MVP (design for later PostgreSQL); queue lives in the DB
- `ModelProvider` adapter interface (OpenAI first) — always keep it swappable
- Lint/typecheck/test: **Ruff**, **Pyright**, **Pytest**
- Run the complete gate before handoff: `uv run ruff format --check .`, `uv run ruff check .`,
  `uv run pyright`, `uv run pytest -q`, then `uv run alembic upgrade head` against an empty DB.
- Use `uv run uvicorn app.main:app --reload` for API and `uv run python -m app.worker` for worker.

## Code map

- `app/domain` owns state vocabulary; `app/application` owns use cases and ports.
- `app/review` owns deterministic diff preparation and output validation.
- `app/github`, `app/models`, and `app/notifications` are outbound adapters.
- `app/storage` owns SQLAlchemy models, repositories, and the leased database queue.
- `app/api` owns the webhook and dashboard HTTP boundaries; `app/worker.py` runs jobs.
- `tests/unit` covers pure policy; `tests/integration` covers DB/API/worker boundaries.

## How to work in this repo

- Business rules live in application/domain services, **not** in API routes or ORM models. GitHub/model/Discord sit behind interfaces so tests can fake them.
- Worker and dashboard share the same service layer.
- **Tests must not make real network calls.** Use `FakeModelProvider`, fixture diffs, and fake GitHub/model/Discord servers.

## Guardrails that must always hold

- Idempotency: one review run per `(repository, pull_number, head_sha)`; dedupe webhook redelivery via `X-GitHub-Delivery`.
- Verify `X-Hub-Signature-256` on the raw body, constant-time compare.
- Don't call model/GitHub/Discord inside the webhook HTTP request — enqueue and return `202`.
- A stale run must NOT post to Discord if `head_sha` is no longer HEAD (mark `superseded`/`skipped`).
- Always send Discord with `allowed_mentions: {"parse": []}`.
- No secrets in logs, API responses, or error messages. Discord webhook URL is encrypted, write-only, allowlisted (HTTPS, Discord hostnames only).
- Treat PR title/body/diff/comments as untrusted; they must never change system instructions or trigger tool/code execution (prompt-injection).
- Record how many files/lines were skipped; a truncated diff is a **partial** review and must be labeled as such, never treated as complete.

## Adopted decisions

OpenAI is the first swappable provider; dashboard authorization uses GitHub OAuth; the master
key comes from deployment secrets; binary/secret/vendor/generated and lock files are ignored
by default; findings may anchor to added or HEAD-context lines; stale runs use `superseded`.
