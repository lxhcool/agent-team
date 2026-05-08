"""Database setup and session management."""

import logging
import sqlite3

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    connect_args={"timeout": 30},
)


@event.listens_for(engine.sync_engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=30000;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.close()

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def init_db():
    """Create tables on startup and apply schema migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Apply incremental schema migrations for existing databases
    await _migrate_schema()


async def _migrate_schema():
    """Add missing columns to existing tables (idempotent)."""
    async with async_session() as db:
        migrations = [
            # ProviderConfig: add user_id column
            ("ALTER TABLE provider_configs ADD COLUMN user_id VARCHAR(36) DEFAULT 'system'", "provider_configs", "user_id"),
            # ModelSettings: add user_id column
            ("ALTER TABLE model_settings ADD COLUMN user_id VARCHAR(36) DEFAULT 'system'", "model_settings", "user_id"),
            # PlanningSession: bind planning workflows to product workspaces
            ("ALTER TABLE planning_sessions ADD COLUMN workspace_id VARCHAR(36)", "planning_sessions", "workspace_id"),
        ]

        for sql, table, column in migrations:
            try:
                # Check if column already exists
                result = await db.execute(text(f"PRAGMA table_info({table})"))
                columns = [row[1] for row in result]
                if column not in columns:
                    await db.execute(text(sql))
                    await db.commit()
                    logger.info(f"Migration applied: added {column} to {table}")
            except Exception as e:
                logger.debug(f"Migration skipped ({table}.{column}): {e}")
                await db.rollback()


async def get_db() -> AsyncSession:
    """Dependency for getting async database sessions."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
