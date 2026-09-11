# Operations runbook

## Database migrations

Back up the SQLite database before upgrading, stop the worker, and run:

```bash
uv run alembic upgrade head
```

Start the API and worker only after the migration succeeds. Test rollback against a copy of
production data before using `alembic downgrade` in production.

### PostgreSQL

Set `DATABASE_URL` to a `postgresql+psycopg://` URL before running migrations. PostgreSQL
workers use row locks with `SKIP LOCKED`, so multiple replicas can claim separate jobs without
serializing behind the oldest queued row. Keep `worker_lease_seconds` longer than a normal model
call; the background heartbeat renews it while processing.

Before increasing worker replicas, verify the database connection limit covers the API pool,
each worker, migrations, and operational access with headroom. The CI PostgreSQL service applies
all migrations and exercises two uncommitted concurrent claims on every pull request.

## Process metrics

The API exposes its local registry at `/metrics`. Each worker exposes its own registry at
`http://WORKER_METRICS_HOST:WORKER_METRICS_PORT/metrics`; the default is
`http://127.0.0.1:9100/metrics`. Compose binds the worker endpoint to `0.0.0.0` but exposes port
9100 only to its internal network. The endpoint has no authentication, so do not publish it to an
untrusted network.

Duration summaries use `_count` and `_sum` series so the collector can calculate an average over
its chosen window:

- `pr_review_queue_latency_seconds_{count,sum}` measures ready-to-claim time.
- `pr_review_duration_seconds_{count,sum}` measures each worker attempt, including failures.
- `pr_review_discord_delivery_latency_seconds_{count,sum}` measures Discord send attempts.
- `pr_review_model_calls_total` and the input/output token counters record returned model usage.
- `pr_review_runs_retried_total`, `pr_review_runs_failed_total`, and
  `pr_review_runs_failed_attempts_total` distinguish retries, terminal failures, and all failed
  attempts.

These series reset when their process restarts. Use the repository usage records described below
for durable token and model-call totals.

## Recovering stuck review jobs

Workers renew `lease_expires_at` while processing. A new worker automatically moves an expired
`running` job back to `queued`. Before intervening manually, verify that the previous worker is
actually stopped so two workers cannot deliver the same result.

Use the dashboard Retry action for a run that exhausted its automatic attempts. The action
requeues the same logical run and preserves its idempotency key.

## Rotating the master key

`review_configs.key_version` records which master-key version encrypted each Discord webhook.
The current MVP runtime accepts one active key, so rotation is an explicit maintenance window:

1. Stop API and worker processes.
2. Back up the database.
3. Decrypt every configured webhook with the old key in a trusted one-off process.
4. Encrypt each value with the new key and increment `key_version` in the same transaction.
5. Replace `MASTER_KEY` in the deployment secret store.
6. Start the API and verify one test notification before starting workers.

Never print plaintext webhooks during rotation. If the old key is lost, stored webhook values
cannot be recovered; replace them through the dashboard.

## Dashboard authorization

Dashboard access is repository-scoped and comes from the signed-in user's current effective
GitHub permissions. Read/triage maps to `viewer`, write/maintain maps to `maintainer`, and admin
maps to `admin`. Repository and organization permission changes take effect on the next request;
the application does not persist a stale role assignment.

Only admins can change review policy or the write-only Discord secret. Maintainers can retry
failed reviews and resend completed Discord notifications. Viewers can inspect review history but
cannot trigger cost-bearing or secret-bearing operations. A `403` after a GitHub role change is
expected; a `404` from GitHub is treated as no access so private repository existence is not
disclosed.

## Usage budgets

Repository budgets use UTC calendar months. A worker reserves the estimated prompt tokens plus
the configured maximum output tokens for every planned model call. Successful and superseded
reviews settle that reservation with actual provider-reported input/output tokens. Automatic
retries retain the same reservation so concurrent or repeated attempts do not reserve twice;
terminal failures release it. Because prompt tokens are estimated before the provider responds,
settled usage can exceed the reservation; the dashboard then reports zero remaining capacity and
later runs cannot reserve more tokens in that period.

If a run is `skipped` with `monthly_token_budget_exceeded`, either wait for the next UTC month or
have a repository admin raise the budget after confirming expected cost. The skipped run remains
immutable; a new PR synchronization event creates a new run. Dashboard usage comes from
`repository_usage` and survives API/worker restarts.

A provider may charge for a request that fails before returning usage metadata. Such unknown
usage cannot be settled exactly from the application; reconcile it against the provider invoice
when investigating cost discrepancies. Never edit `reserved_tokens` directly while a run is
`running` or queued for automatic retry.

## Failure triage

- `GitHub` authentication failures: verify App ID, private key, installation status, and app
  repository permissions.
- `GitHub Checks` failures: verify the App has `Checks: write`, the installation accepted the
  updated permission, and `GITHUB_CHECKS_ENABLED=true`. Delivery failures retain only a stable
  exception class in `github_check_deliveries`; the completed review and Discord delivery remain
  available.
- `OpenAI` failures: verify model access, API key, configured limits, and provider status. For a
  compatible provider, also verify whether it exposes `/responses` or `/chat/completions`, which
  structured-output mode it supports, and whether its token limit field is
  `max_completion_tokens` or `max_tokens`.
- `Discord` failures: `401/403/404` normally mean the write-only webhook must be replaced;
  `429/5xx` are retried automatically.
- Partial reviews: inspect skipped file/line counts and increase repository limits only after
  checking expected model cost.

Persisted errors contain stable error classes/codes only. Use correlated `review_run_id` fields
in structured logs; never enable raw prompt, diff, token, or webhook logging.
