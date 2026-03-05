from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

from gbtd_infra.config import AppConfig
from gbtd_infra.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def get_url():
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return env_url
    return AppConfig().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=Base.metadata,
        literal_binds=True,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        {"sqlalchemy.url": get_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
