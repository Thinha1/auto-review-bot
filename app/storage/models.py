"""SQLAlchemy declarative base.

Domain tables are added here as later PRs land (PR 2 introduces the MVP schema).
Alembic's ``env.py`` imports :data:`Base.metadata` for autogenerate support.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
