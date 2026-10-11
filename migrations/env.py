"""Ponto de entrada do Alembic: diz qual banco usar e quais modelos comparar."""

from alembic import context
from sqlalchemy import create_engine

from transparencia.db.models import Base
from transparencia.settings import get_settings

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Gera o SQL sem conectar (útil para revisar): `alembic upgrade head --sql`."""
    context.configure(url=get_settings().database_url, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_settings().database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
