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

## Failure triage

- `GitHub` authentication failures: verify App ID, private key, installation status, and app
  repository permissions.
- `GitHub Checks` failures: verify the App has `Checks: write`, the installation accepted the
  updated permission, and `GITHUB_CHECKS_ENABLED=true`. Delivery failures retain only a stable
  exception class in `github_check_deliveries`; the completed review and Discord delivery remain
  available.
- `OpenAI` failures: verify model access, API key, configured limits, and provider status.
- `Discord` failures: `401/403/404` normally mean the write-only webhook must be replaced;
  `429/5xx` are retried automatically.
- Partial reviews: inspect skipped file/line counts and increase repository limits only after
  checking expected model cost.

Persisted errors contain stable error classes/codes only. Use correlated `review_run_id` fields
in structured logs; never enable raw prompt, diff, token, or webhook logging.
