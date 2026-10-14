"""Testes de integração com PostgreSQL.

Rodam contra um banco DESCARTÁVEL indicado em TEST_DATABASE_URL (o CI cria um).
Localmente:  crie um banco vazio e rode
    $env:TEST_DATABASE_URL="postgresql+psycopg://transparencia:transparencia@localhost:5432/transparencia_test"
    uv run pytest -m db
Sem a variável, os testes são pulados (skip).
"""

import os

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from transparencia.catalog.loader import carregar_catalogo, carregar_entidades
from transparencia.db.models import Base, Criterion, Entity
from transparencia.db.seed import executar_seed
from transparencia.settings import PROJECT_ROOT, get_settings

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not URL, reason="defina TEST_DATABASE_URL para rodar"),
]

# Diferenças que importam entre o SQL das migrations e os modelos Python.
DIFERENCAS_RELEVANTES = {
    "add_table",
    "remove_table",
    "add_column",
    "remove_column",
    "modify_type",
    "modify_nullable",
    "add_fk",
    "remove_fk",
}


@pytest.fixture(scope="module")
def engine(monkeypatch_module):
    monkeypatch_module.setenv("DATABASE_URL", URL)
    get_settings.cache_clear()
    eng = create_engine(URL)
    with eng.begin() as conn:  # banco limpo
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def _ops(diffs):
    for d in diffs:
        if isinstance(d, list):  # modify_* vem agrupado em listas
            yield from _ops(d)
        else:
            yield d[0]


def test_migrations_batem_com_os_modelos(engine):
    with engine.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    relevantes = [d for d in diffs if set(_ops([d])) & DIFERENCAS_RELEVANTES]
    assert relevantes == [], f"SQL e modelos divergem: {relevantes}"


def test_seed_idempotente(engine):
    catalogo = carregar_catalogo(PROJECT_ROOT / "config" / "criteria_catalog.yaml")
    cadastro = carregar_entidades(PROJECT_ROOT / "config" / "entities.yaml")

    with Session(engine) as s, s.begin():
        r1 = executar_seed(s, catalogo, cadastro)
    with Session(engine) as s, s.begin():
        r2 = executar_seed(s, catalogo, cadastro)

    assert r1.catalogo_novo and not r2.catalogo_novo
    assert r1.entidades_criadas == len(cadastro.entidades)
    assert r2.entidades_criadas == 0 and r2.entidades_atualizadas == 0
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Criterion)) == 142
        assert s.scalar(select(func.count()).select_from(Entity)) == len(cadastro.entidades)


def test_coleta_grava_run_paginas_e_portal_descoberto(engine, tmp_path, monkeypatch):
    """Coleta o site fictício de test_coletor.py (sem rede) e confere o que foi gravado."""
    import httpx

    from tests.test_coletor import SITE, responder
    from transparencia.collector.coletor import ParametrosColeta
    from transparencia.collector.execucao import executar_coleta
    from transparencia.db.models import CrawlRun, EntityUrl, Page

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    cliente = httpx.Client(transport=httpx.MockTransport(responder), follow_redirects=True)
    params = ParametrosColeta(intervalo_por_host_s=0, usar_navegador=False)

    with Session(engine, expire_on_commit=False) as s:
        entity = Entity(
            slug="exemplo",
            nome="Prefeitura de Exemplo",
            municipio="Exemplo",
            uf="RJ",
            poder="executivo",
            esfera="municipal",
        )
        s.add(entity)
        s.flush()
        s.add(EntityUrl(entity_id=entity.id, tipo="site", url=SITE, origem="cadastro"))
        s.commit()
        run = executar_coleta(s, entity, params, cliente=cliente)

    with Session(engine) as s:
        run = s.get(CrawlRun, run.id)
        paginas = s.scalars(select(Page).where(Page.crawl_run_id == run.id)).all()
        portal = s.scalar(
            select(EntityUrl).where(EntityUrl.entity_id == entity.id, EntityUrl.tipo == "portal")
        )
    # PARCIAL: a página bloqueada pelo robots.txt conta como "com erro"
    assert run.status == "PARCIAL"
    assert run.paginas_coletadas + run.paginas_com_erro == len(paginas)
    assert run.fornecedor_detectado == "WordPress"
    assert run.parametros["usar_navegador"] is False
    assert portal.origem == "descoberta" and portal.fornecedor == "WordPress"
    assert all((tmp_path / p.storage_path).exists() for p in paginas if p.storage_path)
