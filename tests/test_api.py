"""Testes da API contra um PostgreSQL descartável (ver tests/conftest.py).

Os dados são montados direto pelo ORM, num cenário pequeno que exercita as
decisões que importam: "mais recente" é pela COLETA (não pela data da
avaliação), o histórico tem um ponto por coleta, e arquivos de evidência só
saem se estiverem registrados e dentro da pasta de dados.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from transparencia.api.app import app, sessao
from transparencia.catalog.loader import carregar_catalogo, carregar_entidades
from transparencia.db.models import (
    CatalogVersion,
    CrawlRun,
    Criterion,
    CriterionResult,
    Entity,
    EvaluationRun,
    Evidence,
    Score,
)
from transparencia.db.seed import executar_seed
from transparencia.settings import PROJECT_ROOT, get_settings

pytestmark = pytest.mark.db


def _t(dia, hora=10):
    return datetime(2026, 10, dia, hora, tzinfo=UTC)


@pytest.fixture(scope="module")
def cenario(engine, tmp_path_factory, monkeypatch_module):
    dados = tmp_path_factory.mktemp("dados")
    (dados / "objetos").mkdir()
    (dados / "objetos" / "pagina.html").write_text("<script>alert(1)</script>ok", "utf-8")
    monkeypatch_module.setenv("DATA_DIR", str(dados))
    get_settings.cache_clear()

    with Session(engine) as s, s.begin():
        executar_seed(
            s,
            carregar_catalogo(PROJECT_ROOT / "config" / "criteria_catalog.yaml"),
            carregar_entidades(PROJECT_ROOT / "config" / "entities.yaml"),
        )

    with Session(engine) as s, s.begin():
        ent = s.scalar(select(Entity).where(Entity.slug == "japeri"))
        versao = s.scalar(select(CatalogVersion))
        crit = {
            c.codigo: c
            for c in s.scalars(select(Criterion).where(Criterion.codigo.in_(["1.1", "1.2"])))
        }
        coleta_a = CrawlRun(entity_id=ent.id, url_inicial="x", status="SUCESSO", started_at=_t(14))
        coleta_b = CrawlRun(entity_id=ent.id, url_inicial="x", status="SUCESSO", started_at=_t(16))
        s.add_all([coleta_a, coleta_b])
        s.flush()

        def avaliacao(coleta, quando, versao_regras, status_11, nota, origem="manual"):
            run = EvaluationRun(
                entity_id=ent.id,
                crawl_run_id=coleta.id,
                catalog_version_id=versao.id,
                versao_regras=versao_regras,
                origem=origem,
                status="SUCESSO",
                started_at=quando,
            )
            s.add(run)
            s.flush()
            valor = {"ATENDE": 1, "NAO_ATENDE": 0}[status_11]
            s.add(
                CriterionResult(
                    evaluation_run_id=run.id,
                    criterion_id=crit["1.1"].id,
                    status=status_11,
                    valor=Decimal(valor),
                    confianca=Decimal("0.9"),
                    metodo="REGRA",
                    justificativa="ok",
                )
            )
            s.add(
                Score(
                    evaluation_run_id=run.id,
                    escopo="geral",
                    valor=Decimal(nota),
                    valor_min=Decimal(nota) - 10,
                    valor_max=Decimal(nota) + 10,
                    cobertura=Decimal(60),
                    nivel="Intermediário",
                    metodologia_versao="t",
                )
            )
            return run

        avaliacao(coleta_a, _t(14), "v1", "NAO_ATENDE", 50)
        recente = avaliacao(coleta_b, _t(16), "v2", "ATENDE", 80)
        # Reprocessamento da coleta ANTIGA, feito DEPOIS: não pode virar a "mais recente".
        avaliacao(coleta_a, _t(16, 12), "v2", "NAO_ATENDE", 50, origem="reprocessamento")

        revisar = CriterionResult(
            evaluation_run_id=recente.id,
            criterion_id=crit["1.2"].id,
            status="NAO_AVALIADO",
            confianca=Decimal(0),
            metodo="REGRA",
            justificativa="dados atrás de consulta",
            revisao_humana=True,
        )
        s.add(revisar)
        s.flush()
        boa = Evidence(
            criterion_result_id=revisar.id,
            tipo="metadado",
            descricao="página",
            url="https://japeri.exemplo/despesas",
            arquivo_path="objetos/pagina.html",
        )
        fora = Evidence(
            criterion_result_id=revisar.id,
            tipo="metadado",
            descricao="caminho malicioso",
            arquivo_path="../../../segredo.txt",
        )
        s.add_all([boa, fora])
        s.flush()
        ids = {"recente": recente.id, "boa": boa.id, "fora": fora.id}

    fabrica = sessionmaker(bind=engine)

    def sessao_de_teste():
        with fabrica() as s:
            yield s

    app.dependency_overrides[sessao] = sessao_de_teste
    yield TestClient(app), ids
    app.dependency_overrides.clear()


def test_saude_e_entidades(cenario):
    c, ids = cenario
    assert c.get("/health").json() == {"status": "ok"}
    entidades = {e["slug"]: e for e in c.get("/entities").json()}
    assert len(entidades) == 7
    ultima = entidades["japeri"]["ultima_avaliacao"]
    assert ultima["id"] == ids["recente"]  # coleta mais recente, apesar do reprocessamento
    assert ultima["nota"]["valor"] == 80 and ultima["revisoes_pendentes"] == 1
    assert entidades["rio-de-janeiro"]["ultima_avaliacao"] is None
    assert c.get("/entities/nao-existe").status_code == 404


def test_ultima_avaliacao_com_evidencias(cenario):
    c, ids = cenario
    a = c.get("/entities/japeri/evaluations/latest").json()
    assert a["id"] == ids["recente"] and a["versao_regras"] == "v2"
    assert [r["criterio"] for r in a["resultados"]] == ["1.1", "1.2"]
    evid = a["resultados"][1]["evidencias"][0]
    assert evid["arquivo"] == f"/evidencias/{ids['boa']}/arquivo"
    assert c.get("/entities/rio-de-janeiro/evaluations/latest").status_code == 404


def test_historico_um_ponto_por_coleta_e_eventos(cenario):
    c, _ = cenario
    h = c.get("/entities/japeri/history").json()
    assert [p["nota"]["valor"] for p in h["pontos"]] == [50, 80]
    assert h["regua_unica"]  # a coleta antiga foi reprocessada com a régua v2
    assert h["pontos"][1]["variacao"] == 30
    assert [(ev["criterio"], ev["evento"]) for ev in h["eventos"]] == [("1.1", "CORRIGIDO")]

    todas = c.get("/entities/japeri/history?todas=true").json()
    assert len(todas["pontos"]) == 3 and not todas["regua_unica"]


def test_arquivo_de_evidencia_seguro(cenario):
    c, ids = cenario
    r = c.get(f"/evidencias/{ids['boa']}/arquivo")
    assert r.status_code == 200
    assert r.headers["content-security-policy"] == "sandbox"  # scripts da página não rodam
    assert c.get(f"/evidencias/{ids['fora']}/arquivo").status_code == 404  # fora de data/
    assert c.get("/evidencias/999999/arquivo").status_code == 404


def test_criterios_e_fila_de_revisao(cenario):
    c, _ = cenario
    assert len(c.get("/criteria").json()) == 142
    poc = c.get("/criteria?poc=true").json()
    assert len(poc) == 19 and all(cr["na_poc"] for cr in poc)
    fila = c.get("/review").json()
    assert [(i["entidade"], i["criterio"]) for i in fila] == [("japeri", "1.2")]
    assert fila[0]["evidencia_url"] == "https://japeri.exemplo/despesas"
