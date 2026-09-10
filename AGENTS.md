# AGENTS.md

## Status

Docs-only project — no source code, no Git repo yet. Everything is planned in two files:
- `pr-review-agent-design.md` — original design (data model, flow, stack, roadmap)
- `IMPLEMENTATION_PLAN.md` — MVP plan (PR breakdown, milestones, guardrails, open decisions)

Both are written in **Vietnamese**. Read them before doing any work; they are the source of truth.

## What it builds

A "PR Review Agent": a GitHub App receives `pull_request` webhooks, queues a review run, an AI worker reviews the diff, and posts structured findings to a per-repo Discord webhook. A dashboard manages multiple repositories. It never merges/approves/rejects — comments only.

## Planned stack (use these when implementing)

- Python **3.12+**, FastAPI, Pydantic, SQLAlchemy + Alembic, HTTPX, Jinja + HTMX (dashboard)
- SQLite for MVP (design for later PostgreSQL); queue lives in the DB
- `ModelProvider` adapter interface (OpenAI first) — always keep it swappable
- Lint/typecheck/test: **Ruff**, **Pyright**, **Pytest**
- No exact commands exist yet — PR 1 is supposed to define the single startup command in `README.md` and wire up Ruff/Pyright/Pytest. Don't invent a toolchain; match PR 1.

## Intended repo layout

`app/` (api, application, github, review, models, notifications, storage, config.py, logging.py, worker.py, main.py), plus `migrations/`, `templates/`, `static/`, `tests/` (unit/integration/e2e + fixtures), `.env.example`, `pyproject.toml`, `README.md`. See `IMPLEMENTATION_PLAN.md` §3.3 for the full tree.

## How to work in this repo

- Implementation is ordered as **PR 1 → 9** across Milestones A/B/C. Build the end-to-end slice first: fixture diff → review engine → DB queue → GitHub webhook → Discord → dashboard. Don't skip ahead past a PR's completion criteria.
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

## Open decisions (finalize in PR 1 before coding further)

Min Python version; OpenAI-only vs. other providers; GitHub OAuth vs. single-operator dashboard; master key via env vs. secret manager; default ignore policy for lock/generated/vendor files; whether findings may anchor to context lines (vs. added-only); `superseded` vs. `skipped` for stale runs. Recommended defaults are listed in `IMPLEMENTATION_PLAN.md` §6.
