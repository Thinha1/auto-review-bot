# MVP acceptance matrix

This matrix maps each Definition of Done item in `IMPLEMENTATION_PLAN.md` to executable
evidence. Run the complete gate in `AGENTS.md` before marking a release.

| Requirement | Evidence |
| --- | --- |
| Two repositories can target different Discord webhooks | `tests/integration/test_worker.py::test_two_repositories_use_different_discord_webhooks` |
| One review run per repository, PR, and HEAD SHA | `tests/integration/test_persistence.py::test_review_run_is_idempotent` |
| GitHub redelivery does not duplicate work | `tests/integration/test_webhook.py::test_webhook_enqueues_once_and_returns_202` |
| Findings reference valid HEAD-side diff lines | `tests/unit/test_diff.py`, `tests/unit/test_review_engine.py` |
| Truncated/ignored input is reported as partial | `tests/unit/test_diff.py::test_prepare_diff_reports_truncation` |
| Failed work has a redacted code and bounded retries | `tests/unit/test_worker_failures.py`, `tests/integration/test_worker.py` |
| Stale HEAD runs never notify Discord | `tests/integration/test_worker.py::test_worker_supersedes_stale_run_without_notification` |
| Disabled repositories and suspended installations stop queued/in-flight delivery | `tests/integration/test_worker.py::test_worker_skips_ineligible_repository_before_external_calls`, `tests/integration/test_worker.py::test_worker_rechecks_eligibility_after_model_before_delivery` |
| Installation suspend/unsuspend preserves repository enable policy | `tests/integration/test_webhook.py::test_installation_suspend_preserves_repository_policy_until_delete` |
| Discord messages suppress all mentions | `tests/unit/test_discord.py::test_formatter_paginates_and_disables_mentions` |
| Secrets are encrypted, write-only, and outbound URLs are allowlisted | `tests/unit/test_security.py`, `tests/integration/test_dashboard.py` |
| Queue claim, lease, and recovery are deterministic | `tests/integration/test_queue.py` |
| Queue/review/Discord latency and retry/model usage metrics are emitted | `tests/unit/test_metrics.py`, `tests/integration/test_worker.py::test_worker_completes_review_and_sends_notification` |
| Responses and compatible Chat Completions providers preserve structured validation | `tests/unit/test_openai_provider.py` |
| Token reservations include the structured-output schema for every model call | `tests/unit/test_review_usage.py::test_estimate_reserves_prompt_and_output_ceiling_for_each_call` |
| Schema upgrades preserve existing configuration | `tests/integration/test_migrations.py` |
| API, worker, migrations, and recovery are documented | `README.md`, `docs/operations.md` |

## Manual release checks

The automated suite never contacts external services. Before the first deployment, use
non-production credentials to verify:

1. Install the GitHub App on two test repositories and confirm both appear in the dashboard.
2. Save a different Discord webhook for each repository; confirm the UI never renders either
   URL back to the browser.
3. Open a small test Pull Request and confirm GitHub receives `202`, one run completes, and its
   Discord link returns to the exact Pull Request.
4. Push two commits quickly and confirm only the latest HEAD sends a Discord message.
5. Revoke one Discord webhook and confirm notification failure is visible and Resend succeeds
   after replacing the webhook.
6. Inspect structured logs and HTTP responses for tokens, webhook URLs, source diff, and prompt
   content before promoting the release.
