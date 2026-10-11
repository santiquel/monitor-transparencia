"""Conexão com o banco.

- `engine`: conexão com o PostgreSQL, com pool de conexões.
- `Session`: unidade de trabalho; as operações de uma sessão formam uma única
  transação: ou tudo é gravado (commit), ou nada é (rollback).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from transparencia.settings import get_settings


@lru_cache
def get_engine() -> Engine:
    # pool_pre_ping: testa a conexão antes de usar (evita erro se o banco reiniciou)
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Uso: `with session_scope() as s: ...` — commit automático, rollback se der erro."""
    factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
