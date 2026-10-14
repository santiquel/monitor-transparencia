"""Testes do coletor, sem rede: um "site fictício" responde pelo httpx.MockTransport.

O MockTransport substitui a rede: cada requisição do httpx chama a função
`responder`, que devolve o HTML que quisermos. Assim testamos a navegação
inteira (descoberta do portal, robots.txt, prioridades, orçamento) em milissegundos.
"""

import httpx
import pytest

from transparencia.collector.armazenamento import Armazenamento
from transparencia.collector.buscadores import BuscadorHttp, Resposta
from transparencia.collector.coletor import Coletor, ParametrosColeta
from transparencia.collector.fornecedor import detectar_fornecedor, escolher_fornecedor
from transparencia.collector.html import analisar_html
from transparencia.collector.robots import PoliticaRobots
from transparencia.collector.urls import caminho, dominio_base, normalizar, resolver
from transparencia.collector.vocabulario import eh_documento, temas_de

UA = "MonitorTransparencia/teste"
SITE = "https://www.exemplo.rj.gov.br/"
PORTAL = "https://transparencia.exemplo.rj.gov.br/"

# Acima de 2.500 caracteres: página de conteúdo, mesmo com poucos links.
TEXTO_LONGO = "Conteúdo da página com texto suficiente. " * 80


def _html(corpo: str, titulo: str = "Página") -> bytes:
    return f"<html><head><title>{titulo}</title></head><body>{corpo}</body></html>".encode()


def _links(*pares: tuple[str, str]) -> str:
    return "".join(f'<a href="{href}">{texto}</a>' for href, texto in pares) + TEXTO_LONGO


PAGINAS = {
    SITE: _html(
        _links(
            ("/noticias", "Notícias"),
            (PORTAL, "Portal da Transparência"),
            ("https://www.facebook.com/prefeitura", "Facebook"),
            ("/a", "A"),
            ("/b", "B"),
        ),
        "Prefeitura",
    ),
    SITE + "noticias": _html(_links(("/noticia-1", "Notícia 1")) + TEXTO_LONGO),
    PORTAL: _html(
        _links(
            ("/despesas", "Despesas"),
            ("/licitacoes", "Licitações"),
            ("/docs/rgf-2026.pdf", "RGF 2026"),
            ("/privado/folha", "Folha de pagamento"),
            ("https://radardatransparencia.atricon.org.br/", "Radar da Transparência"),
            ("https://esic.outrosistema.com.br/pedido", "e-SIC"),
            ("/app", "Dados abertos"),
        )
        + '<link href="/wp-content/tema.css">',
        "Portal",
    ),
    PORTAL + "despesas": _html(_links(("/despesas/consultar", "Consultar"))),
    PORTAL + "despesas/consultar": _html(_links(("/x", "x"))),
    PORTAL + "licitacoes": _html(_links(("/y", "y"))),
    PORTAL + "app": _html('<div id="app"></div>'),  # casca de aplicação JavaScript
    "https://esic.outrosistema.com.br/pedido": _html(_links(("/z", "z"))),
}
ROBOTS = {
    PORTAL + "robots.txt": b"User-agent: *\nDisallow: /privado/\n",
}


def responder(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url in ROBOTS:
        return httpx.Response(200, content=ROBOTS[url])
    if url.endswith("/robots.txt"):
        return httpx.Response(404)
    if url.endswith(".pdf"):
        return httpx.Response(
            200, content=b"%PDF-1.4 rgf", headers={"content-type": "application/pdf"}
        )
    if url in PAGINAS:
        return httpx.Response(200, content=PAGINAS[url], headers={"content-type": "text/html"})
    return httpx.Response(404, content=b"nao encontrado", headers={"content-type": "text/html"})


class NavegadorFalso:
    """Faz o papel do Playwright: devolve a página "renderizada" e um link achado por clique."""

    def __init__(self):
        self.chamadas: list[str] = []

    def buscar(self, url, limite_bytes, filtro_cliques=None):
        self.chamadas.append(url)
        cliques = [(PORTAL + "#/consulta/1", "Servidores")] if filtro_cliques else []
        corpo = _links(("/app/tabela", "Tabela de dados abertos"), ("/c", "c"), ("/d", "d"))
        return Resposta(url, "browser", url, 200, "text/html", _html(corpo), b"png", None, cliques)


def _coletor(tmp_path, navegador=None, **params) -> tuple[Coletor, httpx.Client]:
    cliente = httpx.Client(transport=httpx.MockTransport(responder), follow_redirects=True)
    coletor = Coletor(
        ParametrosColeta(intervalo_por_host_s=0, **params),
        http=BuscadorHttp(cliente),
        robots=PoliticaRobots(cliente, UA),
        armazenamento=Armazenamento(tmp_path),
        navegador=navegador,
        dormir=lambda _: None,
    )
    return coletor, cliente


# --------------------------------------------------------------------- URLs


def test_normalizar():
    assert (
        normalizar("HTTPS://Site.GOV.br:443/a?utm_source=x&id=2#topo")
        == "https://site.gov.br/a?id=2"
    )
    assert normalizar("https://p.betha.cloud/#/ABC==/rota") == "https://p.betha.cloud/#/ABC==/rota"
    assert normalizar("ftp://site.gov.br/") is None


def test_resolver_ignora_o_que_nao_e_pagina():
    base = "https://site.gov.br/pasta/"
    assert resolver(base, "../x") == "https://site.gov.br/x"
    for href in ("mailto:a@b.c", "javascript:void(0)", "#topo", "tel:123", ""):
        assert resolver(base, href) is None


@pytest.mark.parametrize(
    ("host", "esperado"),
    [
        ("portal.duquedecaxias.rj.gov.br", "duquedecaxias.rj.gov.br"),
        ("japeri.geosiap.net.br", "geosiap.net.br"),
        ("transparencia.prefeitura.rio", "prefeitura.rio"),
    ],
)
def test_dominio_base(host, esperado):
    assert dominio_base(host) == esperado


def test_caminho_ignora_o_host():
    assert "transparencia" not in caminho("https://transparencia.betha.cloud/#/abc/despesas")


# --------------------------------------------------------------- vocabulário


def test_temas():
    assert temas_de("Relatório de Gestão Fiscal") == {"gestao_fiscal"}
    assert temas_de("Pedido via e-SIC") == {"esic"}
    assert temas_de("", "/servidores-publicos/remuneracao") == {"pessoal"}
    assert temas_de("Download do arquivo") == set()  # "loa" não casa dentro de "download"


def test_eh_documento():
    assert eh_documento("https://x.gov.br/RGF.PDF?v=1")
    assert not eh_documento("https://x.gov.br/despesas")


# ---------------------------------------------------------------------- HTML


def test_analisar_html_respeita_base_e_le_iframe_e_imagem():
    html = (
        b'<html><head><base href="https://outro.gov.br/raiz/"><title> T </title></head><body>'
        b'<a href="pagina">Texto</a><a href="/icone"><img alt="Acessibilidade"></a>'
        b'<iframe src="https://fornecedor.com.br/portal"></iframe></body></html>'
    )
    a = analisar_html(html, "https://site.gov.br/")
    assert a.titulo == "T"
    assert [(link.url, link.texto, link.tag) for link in a.links] == [
        ("https://outro.gov.br/raiz/pagina", "Texto", "a"),
        ("https://outro.gov.br/icone", "Acessibilidade", "a"),
        ("https://fornecedor.com.br/portal", "[iframe]", "iframe"),
    ]
    assert a.parece_vazia  # 3 links e pouco texto: casca de aplicação JavaScript


# ---------------------------------------------------------------- fornecedor


def test_fornecedor_especifico_vence_wordpress():
    assert detectar_fornecedor("https://japeri.geosiap.net.br/x", b"") == "GeoSiap"
    assert (
        detectar_fornecedor("https://x.gov.br", b'<link href="/wp-content/a.css">') == "WordPress"
    )
    contagem = {"WordPress": 9, "Supernova": 2}
    assert escolher_fornecedor(contagem) == "Supernova"
    assert escolher_fornecedor({"WordPress": 1}) == "WordPress"
    assert escolher_fornecedor({}) is None


# -------------------------------------------------------------------- robots


def _robots(handler) -> PoliticaRobots:
    return PoliticaRobots(httpx.Client(transport=httpx.MockTransport(handler)), UA)


def test_robots_segue_a_rfc_9309():
    def handler(request):
        respostas = {
            "a.gov.br": httpx.Response(200, content=b"User-agent: *\nDisallow: /privado"),
            "b.gov.br": httpx.Response(404),
            "c.gov.br": httpx.Response(503),
        }
        if request.url.host == "d.gov.br":
            raise httpx.ConnectError("recusada")
        return respostas[request.url.host]

    robots = _robots(handler)
    assert robots.avaliar("https://a.gov.br/publico").permitido
    assert not robots.avaliar("https://a.gov.br/privado/x").permitido
    assert robots.avaliar("https://b.gov.br/qualquer").permitido  # 404: tudo liberado
    assert not robots.avaliar("https://c.gov.br/x").permitido  # 5xx: tudo bloqueado
    d = robots.avaliar("https://d.gov.br/x")
    assert not d.permitido and d.host_inalcancavel


def test_robots_vale_por_porta():
    pedidos = []

    def handler(request):
        pedidos.append(str(request.url))
        return httpx.Response(404)

    robots = _robots(handler)
    robots.avaliar("https://s.com.br:8443/a")
    robots.avaliar("https://s.com.br:8446/b")
    robots.avaliar("https://s.com.br:8443/c")  # já em cache
    assert pedidos == ["https://s.com.br:8443/robots.txt", "https://s.com.br:8446/robots.txt"]


def test_robots_pode_ser_ignorado_por_excecao():
    robots = PoliticaRobots(httpx.Client(transport=httpx.MockTransport(responder)), UA, True)
    assert robots.avaliar(PORTAL + "privado/folha").permitido


# ------------------------------------------------------------------- coletor


def test_coleta_descobre_portal_e_respeita_as_regras(tmp_path):
    navegador = NavegadorFalso()
    coletor, _ = _coletor(tmp_path, navegador=navegador)
    r = coletor.coletar(SITE)  # sem portal cadastrado: precisa descobrir
    por_url = {p.url: p for p in r.paginas}

    assert r.portal_descoberto == PORTAL
    assert por_url[PORTAL].papel == "descoberta"
    assert r.fornecedor == "WordPress"

    # robots.txt do portal proíbe /privado/: fica registrado, sem requisição
    bloqueada = por_url[PORTAL + "privado/folha"]
    assert "robots.txt" in bloqueada.erro and bloqueada.fetched_at is None

    # Externos: e-SIC (tema específico) sim; Radar (só "transparência") e Facebook não
    assert "https://esic.outrosistema.com.br/pedido" in por_url
    assert not any("atricon" in u or "facebook" in u for u in por_url)

    # Documento salvo com hash e extensão certa
    pdf = por_url[PORTAL + "docs/rgf-2026.pdf"]
    assert pdf.content_type == "application/pdf"
    assert pdf.storage_path.endswith(".pdf")
    assert (tmp_path / pdf.storage_path).read_bytes() == b"%PDF-1.4 rgf"

    # Página "vazia" no HTTP foi buscada de novo no navegador, com screenshot
    app = por_url[PORTAL + "app"]
    assert app.modo_render == "browser" and app.screenshot_path
    assert PORTAL + "app/tabela" in por_url  # link que só existia após o JavaScript

    # Link genérico ("Consultar") seguido só porque a página de origem é temática
    assert PORTAL + "despesas/consultar" in por_url
    assert SITE + "noticia-1" not in por_url  # genérico a partir de página sem tema


def test_cliques_so_na_pagina_inicial_do_portal(tmp_path):
    navegador = NavegadorFalso()
    coletor, _ = _coletor(tmp_path, navegador=navegador)
    r = coletor.coletar(SITE, PORTAL + "app")
    por_url = {p.url: p for p in r.paginas}
    assert PORTAL + "#/consulta/1" in por_url
    assert por_url[PORTAL + "#/consulta/1"].texto_link == "Servidores"


def test_orcamento_de_paginas(tmp_path):
    coletor, _ = _coletor(tmp_path, max_paginas=3, max_documentos=0)
    r = coletor.coletar(SITE, PORTAL)
    requisitadas = [p for p in r.paginas if p.fetched_at is not None]
    assert len(requisitadas) == 3
    assert not any(eh_documento(p.url) for p in r.paginas)


def test_sem_navegador_segue_so_com_http(tmp_path):
    coletor, _ = _coletor(tmp_path, navegador=None)
    r = coletor.coletar(SITE, PORTAL)
    assert {p.modo_render for p in r.paginas if p.modo_render} == {"http"}


def test_armazenamento_deduplica(tmp_path):
    arm = Armazenamento(tmp_path)
    h1, c1 = arm.salvar(b"igual", ".html")
    h2, c2 = arm.salvar(b"igual", ".html")
    assert (h1, c1) == (h2, c2)
    assert c1 == f"objetos/{h1[:2]}/{h1}.html"
    assert len(list(tmp_path.rglob("*.html"))) == 1


def test_normalizar_recusa_armadilha_de_redirect():
    armadilha = "https://x.rio/c/portal/login?redirect=%25252Fc%25252Fportal%25252Flogin"
    assert normalizar(armadilha) is None
    assert normalizar("https://x.rio/" + "a" * 600) is None


def test_site_tem_cota_reservada(tmp_path):
    # 8 páginas: cota do site = 8 // 4 = 2; portal = 6, por mais temático que seja.
    coletor, _ = _coletor(tmp_path, max_paginas=8, max_documentos=0)
    r = coletor.coletar(SITE, PORTAL)
    requisitadas = [p for p in r.paginas if p.fetched_at is not None]
    do_site = [p for p in requisitadas if p.url.startswith(SITE)]
    assert len(do_site) == 2
    assert len(requisitadas) <= 8
