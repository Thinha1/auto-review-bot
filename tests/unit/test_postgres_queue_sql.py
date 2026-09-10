from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from app.storage.queue import claim_candidate_statement


def test_postgres_claim_uses_skip_locked() -> None:
    statement = claim_candidate_statement(datetime.now(UTC), skip_locked=True)
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "review_runs.status" in sql
