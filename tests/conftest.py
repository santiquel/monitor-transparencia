"""Fixtures compartilhadas pelos testes (o pytest carrega este arquivo sozinho).

`engine`: um PostgreSQL DESCARTÁVEL (TEST_DATABASE_URL), zerado e migrado no
início de cada arquivo de teste que o usa. Sem a variável, esses testes são
pulados (o CI define a variável e cria o banco).
"""

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from transparencia.settings import PROJECT_ROOT, get_settings

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def engine(monkeypatch_module):
    if not TEST_DATABASE_URL:
        pytest.skip("defina TEST_DATABASE_URL para rodar")
    monkeypatch_module.setenv("DATABASE_URL", TEST_DATABASE_URL)
    get_settings.cache_clear()
    eng = create_engine(TEST_DATABASE_URL)
    with eng.begin() as conn:  # banco limpo
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()
    get_settings.cache_clear()
