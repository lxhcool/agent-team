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

        try:
            result = await db.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='workspace_memories'"))
            exists = result.scalar_one_or_none()
            if not exists:
                await db.execute(text("""
                    CREATE TABLE workspace_memories (
                        id VARCHAR(36) PRIMARY KEY,
                        workspace_id VARCHAR(36) NOT NULL,
                        stage_key VARCHAR(50) NOT NULL,
                        source_message_id VARCHAR(36),
                        source_artifact_id VARCHAR(36),
                        memory_type VARCHAR(50) NOT NULL,
                        topic VARCHAR(120) NOT NULL DEFAULT '',
                        content TEXT NOT NULL,
                        status VARCHAR(20) NOT NULL DEFAULT 'confirmed',
                        scope VARCHAR(20) NOT NULL DEFAULT 'global',
                        supersedes_memory_id VARCHAR(36),
                        tags_json TEXT,
                        created_at DATETIME,
                        updated_at DATETIME
                    )
                """))
                await db.execute(text("CREATE INDEX ix_workspace_memories_workspace_id ON workspace_memories (workspace_id)"))
                await db.execute(text("CREATE INDEX ix_workspace_memories_stage_key ON workspace_memories (stage_key)"))
                await db.execute(text("CREATE INDEX ix_workspace_memories_memory_type ON workspace_memories (memory_type)"))
                await db.execute(text("CREATE INDEX ix_workspace_memories_topic ON workspace_memories (topic)"))
                await db.execute(text("CREATE INDEX ix_workspace_memories_status ON workspace_memories (status)"))
                await db.execute(text("CREATE INDEX ix_workspace_memories_scope ON workspace_memories (scope)"))
                await db.execute(text("CREATE INDEX ix_workspace_memories_source_message_id ON workspace_memories (source_message_id)"))
                await db.execute(text("CREATE INDEX ix_workspace_memories_source_artifact_id ON workspace_memories (source_artifact_id)"))
                await db.execute(text("CREATE INDEX ix_workspace_memories_supersedes_memory_id ON workspace_memories (supersedes_memory_id)"))
                await db.commit()
                logger.info("Migration applied: created workspace_memories table")
        except Exception as e:
            logger.debug(f"Migration skipped (workspace_memories): {e}")
            await db.rollback()

        try:
            result = await db.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='workspace_stage_reviews'"))
            exists = result.scalar_one_or_none()
            if not exists:
                await db.execute(text("""
                    CREATE TABLE workspace_stage_reviews (
                        id VARCHAR(36) PRIMARY KEY,
                        workspace_id VARCHAR(36) NOT NULL,
                        stage_id VARCHAR(36) NOT NULL,
                        stage_key VARCHAR(50) NOT NULL,
                        status VARCHAR(30) NOT NULL DEFAULT 'completed',
                        review_type VARCHAR(50) NOT NULL DEFAULT 'expert_group',
                        draft_message_id VARCHAR(36),
                        participants_json TEXT,
                        expert_findings_json TEXT,
                        summary TEXT,
                        result_json TEXT,
                        created_by VARCHAR(36),
                        created_at DATETIME,
                        updated_at DATETIME
                    )
                """))
                await db.execute(text("CREATE INDEX ix_workspace_stage_reviews_workspace_id ON workspace_stage_reviews (workspace_id)"))
                await db.execute(text("CREATE INDEX ix_workspace_stage_reviews_stage_id ON workspace_stage_reviews (stage_id)"))
                await db.execute(text("CREATE INDEX ix_workspace_stage_reviews_stage_key ON workspace_stage_reviews (stage_key)"))
                await db.execute(text("CREATE INDEX ix_workspace_stage_reviews_status ON workspace_stage_reviews (status)"))
                await db.execute(text("CREATE INDEX ix_workspace_stage_reviews_review_type ON workspace_stage_reviews (review_type)"))
                await db.execute(text("CREATE INDEX ix_workspace_stage_reviews_draft_message_id ON workspace_stage_reviews (draft_message_id)"))
                await db.execute(text("CREATE INDEX ix_workspace_stage_reviews_created_by ON workspace_stage_reviews (created_by)"))
                await db.commit()
                logger.info("Migration applied: created workspace_stage_reviews table")
        except Exception as e:
            logger.debug(f"Migration skipped (workspace_stage_reviews): {e}")
            await db.rollback()


async def get_db() -> AsyncSession:
    """Dependency for getting async database sessions."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
