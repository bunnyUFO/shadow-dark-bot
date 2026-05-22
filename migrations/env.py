from sqlalchemy import engine_from_config, pool

from alembic import context

from shadowdark_bot.config import settings
from shadowdark_bot.models import Base

# Note: we intentionally do NOT call logging.config.fileConfig() here.
# main.py configures logging via basicConfig, and fileConfig would (a) silence
# all existing loggers (disable_existing_loggers defaults to True) and
# (b) lower the root logger to WARN per alembic.ini's defaults. That hides
# every shadowdark_bot log line emitted after migrations finish. Alembic's
# own loggers still emit at INFO via propagation to the root handler.
config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
