"""
Alembic migration environment — async-compatible.

Uses asyncpg + SQLAlchemy async engine so migrations run against the same
engine configuration as the application.

Run migrations:
  alembic upgrade head
  alembic downgrade -1
  alembic revision --autogenerate -m "description"
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ── Load application config + models ─────────────────────────────────────────
# Import Base so Alembic can inspect all mapped tables for autogenerate.
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.config import get_settings
from app.database import Base

# Import all models so their tables are registered on Base.metadata
import app.models.db  # noqa: F401

# ── Alembic config ────────────────────────────────────────────────────────────
config = context.config

# Inject the real database URL from Settings (overrides alembic.ini placeholder)
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

# Configure Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ── Offline mode (generates SQL without connecting) ───────────────────────────

def run_migrations_offline() -> None:
    """
    Generate migration SQL without an active DB connection.
    Useful for review or for applying migrations via DBA.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url                      = url,
        target_metadata          = target_metadata,
        literal_binds            = True,
        dialect_opts             = {"paramstyle": "named"},
        compare_type             = True,    # detect column type changes
        compare_server_default   = True,    # detect default value changes
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online mode (connects and runs migrations directly) ───────────────────────

def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection             = connection,
        target_metadata        = target_metadata,
        compare_type           = True,
        compare_server_default = True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using the async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix          = "sqlalchemy.",
        poolclass       = pool.NullPool,    # no persistent pool for migrations
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


# ── Entry point ───────────────────────────────────────────────────────────────

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
