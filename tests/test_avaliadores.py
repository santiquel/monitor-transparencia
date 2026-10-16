"""Testes dos avaliadores com HTML sintético (sem banco, sem rede).

Cada teste monta um "snapshot" mínimo: páginas HTML gravadas numa pasta
temporária e objetos `Page`/`CrawlRun` em memória (não precisam de banco).
"""

from datetime import UTC, date, datetime

import pytest

from transparencia.db.models import CrawlRun, Criterion, Entity, Page
from transparencia.db.models import StatusConformidade as S
from transparencia.evaluation.avaliadores import AVALIADORES, bimestre_exigivel
from transparencia.evaluation.motor import avaliar_criterios
from transparencia.evaluation.snapshot import PaginaSnapshot, Snapshot

SITE = "https://www.exemplo.rj.gov.br/"
PORTAL = "https://transparencia.exemplo.rj.gov.br/"
COLETA = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
TEXTO = "<p>" + "Texto institucional da prefeitura. " * 20 + "</p>"


class Montador:
    """Monta um Snapshot a partir de pares (url, html)."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.paginas: list[PaginaSnapshot] = []

    def pagina(self, url, corpo, profundidade=1, texto_link=None, titulo="Página", **kw):
        n = len(self.paginas) + 1
        arquivo = f"p{n}.html"
        html = f"<html><head><title>{titulo}</title></head><body>{corpo}</body></html>"
        (self.tmp / arquivo).write_text(html, encoding="utf-8")
        page = Page(
            id=n,
            url=url,
            url_final=url,
            profundidade=profundidade,
            http_status=200,
            content_type="text/html",
            storage_path=arquivo,
            texto_link=texto_link,
            titulo=titulo,
            fetched_at=COLETA,
            **kw,
        )
        self.paginas.append(PaginaSnapshot(page, self.tmp))
        return self

    def snapshot(self, max_paginas=60) -> Snapshot:
        run = CrawlRun(
            id=1,
            url_inicial=PORTAL,
            started_at=COLETA,
            parametros={"url_site": SITE, "max_paginas": max_paginas},
        )
        return Snapshot(run, SITE, PORTAL, self.paginas)


@pytest.fixture
def m(tmp_path):
    return Montador(tmp_path)


def _avaliar(codigo, snap):
    return AVALIADORES[codigo](snap)


# ---------------------------------------------------------------- 1.2 / 1.3


def test_1_2_link_visivel_rodape_ou_ausente(m):
    snap = m.pagina(
        SITE, f'<header><a href="{PORTAL}">Transparência</a></header>{TEXTO}', 0
    ).snapshot()
    v = _avaliar("1.2", snap)
    assert v.status == S.ATENDE and v.evidencias[0].seletor.startswith("header")

    m.paginas.clear()
    snap = m.pagina(SITE, f'{TEXTO}<footer><a href="{PORTAL}">Portal</a></footer>', 0).snapshot()
    assert _avaliar("1.2", snap).status == S.ATENDE_PARCIALMENTE

    m.paginas.clear()
    snap = m.pagina(SITE, f'<a href="/noticias">Notícias</a>{TEXTO}', 0).snapshot()
    assert _avaliar("1.2", snap).status == S.NAO_ATENDE


@pytest.mark.parametrize(
    ("html", "esperado"),
    [
        # Wix: o componente do menu principal se chama "dropdown-menu", mas o item
        # de primeiro nível é visível (achado na revisão humana de Cachoeiras, 1.2).
        (
            '<wix-dropdown-menu class="wixui-dropdown-menu"><nav><ul>'
            '<li class="wixui-dropdown-menu__item"><a href="{p}">TRANSPARÊNCIA</a></li>'
            "</ul></nav></wix-dropdown-menu>",
            "visivel",
        ),
        # Bootstrap: li.dropdown é o botão visível...
        (
            '<nav><ul class="navbar-nav"><li class="nav-item dropdown">'
            '<a href="{p}">Transparência</a></li></ul></nav>',
            "visivel",
        ),
        # ...e div.dropdown-menu é o painel que só abre ao clicar.
        (
            '<nav><div class="dropdown"><button>Governo</button><div class="dropdown-menu">'
            '<a class="dropdown-item" href="{p}">Transparência</a></div></div></nav>',
            "submenu",
        ),
        # WordPress: ul.sub-menu dentro de um li (como no site do Rio).
        (
            '<nav><ul class="menu"><li><a href="/gov">Governo</a><ul class="sub-menu">'
            '<li><a href="{p}">Transparência</a></li></ul></li></ul></nav>',
            "submenu",
        ),
    ],
)
def test_1_2_posicao_do_link_por_biblioteca_de_menu(m, html, esperado):
    snap = m.pagina(SITE, html.format(p=PORTAL) + TEXTO, 0).snapshot()
    v = _avaliar("1.2", snap)
    status = S.ATENDE if esperado == "visivel" else S.ATENDE_PARCIALMENTE
    assert v.status == status, v.justificativa


def test_1_2_sem_pagina_do_site_nao_avalia(m):
    snap = m.pagina(PORTAL, TEXTO, 0).snapshot()
    assert _avaliar("1.2", snap).status == S.NAO_AVALIADO


def test_1_3_campo_de_busca(m):
    snap = m.pagina(
        SITE, f'<form><input name="s" placeholder="Pesquisar"></form>{TEXTO}', 0
    ).snapshot()
    v = _avaliar("1.3", snap)
    assert v.status == S.ATENDE and v.confianca == 0.9


# ------------------------------------------------------------ acessibilidade


def test_16_4_simbolo_no_site_e_no_portal(m):
    simbolo = '<a href="/acessibilidade"><img alt="Acessibilidade"></a>'
    snap = m.pagina(SITE, simbolo + TEXTO, 0).pagina(PORTAL, simbolo + TEXTO, 0).snapshot()
    assert _avaliar("16.4", snap).status == S.ATENDE

    m.paginas.clear()
    snap = (
        m.pagina(SITE, TEXTO, 0)
        .pagina(PORTAL, '<div vw class="enabled"></div>' + TEXTO, 0)
        .snapshot()
    )
    v = _avaliar("16.4", snap)
    assert v.status == S.ATENDE_PARCIALMENTE and "portal" in v.justificativa


def test_16_3_botoes_a_mais_a_menos(m):
    snap = m.pagina(SITE, '<button id="zoom-out">A-</button>' + TEXTO, 0).snapshot()
    assert _avaliar("16.3", snap).status == S.ATENDE


# ------------------------------------------------------------- exportação


def test_4_5_formatos_e_falso_positivo_em_codigo(m):
    corpo = (
        "<pre><code><span>representante_doc</span></code></pre>"
        '<img src="/swagger.json">'
        '<a href="/despesas.csv">CSV</a><button><i class="fa fa-file-pdf"></i></button>'
    )
    snap = m.pagina(PORTAL + "despesas", corpo + TEXTO, texto_link="Despesas").snapshot()
    v = _avaliar("4.5", snap)
    assert v.status == S.ATENDE
    assert {e.descricao for e in v.evidencias} == {"Exportação em CSV", "Exportação em PDF"}


def test_4_5_so_pdf_e_parcial(m):
    snap = m.pagina(
        PORTAL + "despesas", '<a href="/r.pdf">PDF</a>' + TEXTO, texto_link="Despesas"
    ).snapshot()
    assert _avaliar("4.5", snap).status == S.ATENDE_PARCIALMENTE


# -------------------------------------------------------------- tabelas


def test_5_1_lista_nominal(m):
    tabela = (
        "<table><tr><th>Nome</th><th>Cargo</th></tr>"
        "<tr><td>Ana</td><td>Professora</td></tr></table>"
    )
    snap = m.pagina(PORTAL + "servidores", tabela + TEXTO, texto_link="Servidores").snapshot()
    assert _avaliar("5.1", snap).status == S.ATENDE

    m.paginas.clear()
    sem_nome = (
        "<table><tr><th>Matrícula</th><th>Cargo</th></tr><tr><td>1</td><td>X</td></tr></table>"
    )
    snap = m.pagina(PORTAL + "servidores", sem_nome + TEXTO, texto_link="Servidores").snapshot()
    assert _avaliar("5.1", snap).status == S.ATENDE_PARCIALMENTE


def test_pagina_certa_sem_dados_e_zona_cinzenta(m):
    form = '<form><select name="ano"></select><button>Consultar</button></form>'
    snap = m.pagina(PORTAL + "servidores", form + TEXTO, texto_link="Servidores").snapshot()
    v = _avaliar("5.1", snap)
    assert v.status == S.NAO_AVALIADO and v.revisao_humana


# ---------------------------------------------------- ausência e orçamento


def test_ausencia_so_conclui_se_a_coleta_nao_parou_por_orcamento(m):
    m.pagina(SITE, TEXTO, 0).pagina(PORTAL, TEXTO, 0)
    assert _avaliar("7.1", m.snapshot(max_paginas=60)).status == S.NAO_ATENDE
    assert _avaliar("7.1", m.snapshot(max_paginas=2)).status == S.NAO_AVALIADO


# ------------------------------------------------------------ documentos


def test_documentos_com_ano_no_texto_da_lista(m):
    lista = (
        "<ul><li>Relatório de Gestão Fiscal – 2º Quadrimestre / 2026</li>"
        "<li>LOA 2025</li><li>RREO 3º Bimestre 2026</li></ul>"
    )
    snap = m.pagina(PORTAL + "lrf", lista + TEXTO, texto_link="LRF").snapshot()
    assert _avaliar("12.1", snap).status == S.ATENDE
    assert _avaliar("19.5", snap).status == S.ATENDE_PARCIALMENTE  # LOA do ano anterior
    v = _avaliar("20.2", snap)  # exigível em 14/10/2026: 4º bimestre
    assert v.status == S.ATENDE_PARCIALMENTE and v.evidencias[0].metadados["atraso_bimestres"] == 1


def test_bimestre_exigivel():
    assert bimestre_exigivel(date(2026, 10, 14)) == (2026, 4)  # jul-ago, venceu em 30/09
    assert bimestre_exigivel(date(2026, 9, 29)) == (2026, 3)
    assert bimestre_exigivel(date(2026, 1, 15)) == (2025, 5)  # 6º/2025 vence em 30/01


# ------------------------------------------------------------ atualização


def test_4_6_ignora_data_do_cabecalho_e_usa_celulas(m):
    corpo = (
        "<header>Hoje é 14/10/2026</header>"
        "<table><tr><th>Data</th><th>Valor</th></tr><tr><td>20/09/2026</td><td>10</td></tr></table>"
    )
    snap = m.pagina(PORTAL + "despesas", corpo + TEXTO, texto_link="Despesas").snapshot()
    v = _avaliar("4.6", snap)
    assert v.status == S.ATENDE and v.evidencias[0].metadados["dias"] == 24


# Evidência negativa exige fonte completa (achados da 1ª medição contra o golden set)


def test_4_6_data_antiga_em_tabela_nao_prova_atraso(m):
    tabela = "<table><tr><th>Data</th><th>Valor</th></tr><tr><td>01/03/2025</td><td>1</td></tr>"
    snap = m.pagina(PORTAL + "despesas", tabela + "</table>" + TEXTO, texto_link="Despesas")
    v = _avaliar("4.6", snap.snapshot())
    assert v.status == S.NAO_AVALIADO and v.revisao_humana


def test_4_6_atraso_declarado_pelo_portal_e_nao_atende(m):
    corpo = "<p>Dados atualizados em 01/03/2025.</p>"
    snap = m.pagina(PORTAL + "despesas", corpo + TEXTO, texto_link="Despesas").snapshot()
    assert _avaliar("4.6", snap).status == S.NAO_ATENDE


def test_pagina_indicada_pelo_revisor_vem_primeiro(m):
    from transparencia.collector.vocabulario import texto_link_indicado

    folha = "<table><tr><th>Nome</th><th>Salário</th></tr></table>"  # parece despesa, não é
    snap = (
        m.pagina(PORTAL + "agrupador/31207", folha + TEXTO, texto_link="Despesas com pessoal")
        .pagina(PORTAL + "consulta/despesas", TEXTO, texto_link=texto_link_indicado("4.1"))
        .snapshot()
    )
    assert [p.url for p in snap.com_tema("despesa")] == [
        PORTAL + "consulta/despesas",
        PORTAL + "agrupador/31207",
    ]
    v = _avaliar("4.1", snap)  # sem tabela de despesa visível: aponta a página indicada
    assert v.status == S.NAO_AVALIADO and v.evidencias[0].url == PORTAL + "consulta/despesas"


def test_4_6_metadado_de_publicacao_do_cms_nao_e_declaracao(m):
    corpo = "<p>Publicado em 19/08/2019 - 15:23 | Atualizado em 30/04/2025 - 18:13</p>"
    snap = m.pagina(PORTAL + "despesas", corpo + TEXTO, texto_link="Despesas").snapshot()
    assert _avaliar("4.6", snap).status == S.NAO_AVALIADO  # sem dado: não acusa


def test_4_5_sem_exportacao_visivel_nao_acusa(m):
    snap = m.pagina(PORTAL + "despesas", TEXTO, texto_link="Despesas").snapshot()
    v = _avaliar("4.5", snap)
    assert v.status == S.NAO_AVALIADO and "ausência não está comprovada" in v.justificativa


def test_20_2_atraso_visto_em_lista_parcial_nao_acusa(m):
    lista = "<ul><li>RREO - 6º BIMESTRE 2025</li><li>RREO - 3º BIMESTRE 2025</li></ul>"
    snap = m.pagina(PORTAL + "lrf", lista + TEXTO, texto_link="LRF").snapshot()
    v = _avaliar("20.2", snap)  # exigível em 14/10/2026: 4º/2026 -> 4 bimestres antes
    assert v.status == S.NAO_AVALIADO and v.evidencias[0].metadados["atraso_bimestres"] == 4


def test_7_1_seis_de_sete_campos_e_parcial(m):
    cab = ["Número", "Modalidade", "Objeto", "Data", "Valor", "Situação"]  # sem vencedor
    tabela = "<table><tr>" + "".join(f"<th>{c}</th>" for c in cab) + "</tr></table>"
    snap = m.pagina(PORTAL + "licitacoes", tabela + TEXTO, texto_link="Licitações").snapshot()
    v = _avaliar("7.1", snap)
    assert v.status == S.ATENDE_PARCIALMENTE and "vencedor" in v.justificativa


def test_18_1_ignora_cargo_e_pagina_do_fornecedor(m):
    cargo = "<p>Encarregado de obras: João, telefone (21) 2222-3333.</p>"
    fornecedor = "<p>Encarregado pelo tratamento de dados pessoais: dpo@fornecedor.com.br</p>"
    snap = (
        m.pagina(PORTAL + "servidores", cargo + TEXTO, texto_link="Servidores")
        .pagina("https://www.fornecedor.com.br/privacidade", fornecedor + TEXTO)
        .snapshot()
    )
    assert _avaliar("18.1", snap).status == S.NAO_ATENDE  # nenhuma menção válida

    m.pagina(SITE + "lgpd", fornecedor.replace("fornecedor.com.br", "exemplo.rj.gov.br") + TEXTO)
    v = _avaliar("18.1", m.snapshot())
    assert v.status == S.ATENDE and v.evidencias[0].url == SITE + "lgpd"


# ------------------------------------------------------------ semântica


def test_15_2_exigencia_explicita(m):
    corpo = "<p>Para registrar o pedido, anexar cópia do documento de identidade.</p>"
    snap = m.pagina(PORTAL + "esic", corpo + TEXTO, texto_link="e-SIC").snapshot()
    v = _avaliar("15.2", snap)
    assert v.status == S.NAO_ATENDE and "documento" in v.evidencias[0].trecho


# ------------------------------------------------------------------ motor


def test_motor_aplicabilidade_e_evidencia_obrigatoria(m):
    snap = m.pagina(SITE, TEXTO, 0).snapshot()
    camara = Entity(slug="camara", poder="legislativo", esfera="municipal")
    loa = Criterion(codigo="19.5", aplica_a=["executivo:*"])
    resultado = avaliar_criterios(snap, camara, [loa])[0].veredito
    assert resultado.status == S.NAO_APLICAVEL and resultado.valor is None
