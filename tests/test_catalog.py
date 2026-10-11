"""Testes do catálogo de critérios (não precisam de banco)."""

from pathlib import Path

import pytest

from transparencia.catalog.applicability import criterio_aplicavel
from transparencia.catalog.builder import construir_catalogo, inferir_dimensao
from transparencia.catalog.loader import carregar_catalogo
from transparencia.catalog.schema import Dimensao, Esfera, Poder
from transparencia.settings import PROJECT_ROOT

XLSX = PROJECT_ROOT / "docs" / "referencia" / "checklistPortalTransp.xlsx"
YAML = PROJECT_ROOT / "config" / "criteria_catalog.yaml"


@pytest.fixture(scope="module")
def catalogo():
    return construir_catalogo(XLSX)


def test_estrutura_do_checklist(catalogo):
    assert len(catalogo.categorias) == 21
    assert len(catalogo.criterios) == 142
    assert catalogo.categoria("19").matriz.value == "especifica_executivo"


def test_yaml_versionado_esta_sincronizado_com_a_planilha(catalogo):
    """Falha se alguém editou o YAML à mão ou mudou o gerador sem regenerar."""
    assert carregar_catalogo(Path(YAML)).content_hash() == catalogo.content_hash()


def test_auditoria_da_fonte_aponta_inconsistencias(catalogo):
    obs = " | ".join(catalogo.observacoes_fonte)
    assert "13.1" in obs
    assert "LC nº 101/00" in obs


def test_limpeza_de_texto(catalogo):
    c = next(x for x in catalogo.criterios if x.codigo == "15.2")
    assert "e-SIC" in c.descricao and "­" not in c.descricao


def test_fundamentacao_herdada_das_celulas_mescladas(catalogo):
    c41 = next(x for x in catalogo.criterios if x.codigo == "4.1")
    c42 = next(x for x in catalogo.criterios if x.codigo == "4.2")
    assert not c41.fundamentacao_herdada
    assert c42.fundamentacao_herdada and c42.fundamentacao == c41.fundamentacao


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Ferramenta de pesquisa específica ...", Dimensao.FILTRO_PESQUISA),
        ("Gravação de relatórios em diversos formatos", Dimensao.GRAVACAO_RELATORIOS),
        ("Existência de informações atualizadas (RREO)", Dimensao.ATUALIDADE),
        ("Existência de histórico das informações", Dimensao.SERIE_HISTORICA),
        ("Relação nominal dos servidores", Dimensao.DISPONIBILIDADE),
    ],
)
def test_inferir_dimensao(texto, esperado):
    assert inferir_dimensao(texto) == esperado


@pytest.mark.parametrize(
    ("codigo", "poder", "esfera", "aplicavel"),
    [
        ("4.1", Poder.LEGISLATIVO, Esfera.MUNICIPAL, True),  # matriz comum: todos
        ("19.5", Poder.EXECUTIVO, Esfera.ESTADUAL, True),  # específica do Executivo
        ("19.5", Poder.LEGISLATIVO, Esfera.MUNICIPAL, False),
        ("21.3", Poder.EXECUTIVO, Esfera.MUNICIPAL, True),  # creches: só municipal
        ("21.3", Poder.EXECUTIVO, Esfera.ESTADUAL, False),
        ("21.2", Poder.EXECUTIVO, Esfera.ESTADUAL, True),
    ],
)
def test_aplicabilidade(catalogo, codigo, poder, esfera, aplicavel):
    crit = next(x for x in catalogo.criterios if x.codigo == codigo)
    assert criterio_aplicavel(crit, poder, esfera) is aplicavel
