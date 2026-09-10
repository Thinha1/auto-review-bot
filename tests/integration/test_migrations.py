import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


def test_new_non_null_limits_backfill_existing_configs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "upgrade.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    config = Config("alembic.ini")
    command.upgrade(config, "bb37708c03df")

    connection = sqlite3.connect(database_path)
    connection.execute(
        "INSERT INTO github_installations "
        "(id, github_installation_id, created_at) VALUES (1, 1, '2026-09-10')"
    )
    connection.execute(
        "INSERT INTO repositories "
        "(id, github_repository_id, github_installation_id, owner, name, enabled, created_at) "
        "VALUES (1, 2, 1, 'octo', 'repo', 1, '2026-09-10')"
    )
    connection.execute(
        "INSERT INTO review_configs "
        "(id, repository_id, key_version, model, minimum_severity, ignored_paths, "
        "trigger_events, ignore_drafts, max_files, max_diff_lines, max_findings, "
        "max_input_tokens, created_at) VALUES "
        "(1, 1, 1, 'model', 'medium', '[]', '[]', 1, 10, 100, 5, 1000, '2026-09-10')"
    )
    connection.commit()
    connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    value = connection.execute("SELECT max_model_calls FROM review_configs WHERE id = 1").fetchone()
    connection.close()
    assert value == (20,)
