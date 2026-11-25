"""Database engine and session management."""

from __future__ import annotations

from collections.abc import Generator
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from slbm_tracker.config import get_settings

settings = get_settings()

# Configure SQLite appropriately
connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
)


if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
        """Harden SQLite for concurrent readers/writers.

        Phase 4 added a background scheduler that commits on the event-loop
        thread while sync REST handlers commit on the threadpool — a new
        write/write overlap. WAL lets a writer proceed alongside readers, and
        ``busy_timeout`` makes a contended writer wait briefly instead of
        immediately raising ``OperationalError: database is locked`` (which would
        surface as an HTTP 500 on a user request). ``foreign_keys=ON`` makes
        SQLite actually enforce FK constraints (OFF by default per connection),
        so a stray orphaned row can never slip past a raw delete path. All are
        no-ops / harmless for an in-memory DB. Applied per connection so pooled
        reconnects keep them.
        """
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """Yield a database session and safely close it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(target_engine=None) -> None:
    """Initialize database schemas."""
    # Import all models before create_all so metadata is populated
    import slbm_tracker.db.models  # noqa: F401
    
    from slbm_tracker.db.migrate import run_migrations

    eng = target_engine or engine
    Base.metadata.create_all(bind=eng)
    # create_all never adds columns to existing tables; additive columns do.
    run_migrations(eng)
