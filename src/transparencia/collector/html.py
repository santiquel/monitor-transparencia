"""Leitura do HTML: título, links e quantidade de texto visível.

Também decide se a página "veio vazia" no HTTP simples, sinal de que o
conteúdo é montado por JavaScript e precisa de navegador real.
"""

import warnings
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from transparencia.collector.urls import resolver

# Páginas XHTML (comuns em sistemas Java/JSF) são lidas como HTML de propósito.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Sinais de "casca" de aplicação JavaScript (calibrados no reconhecimento dos portais:
# Betha veio com 0 links; Duque de Caxias, com 1 link e ~1.650 caracteres).
MIN_LINKS = 5
MIN_CARACTERES_TEXTO = 250  # menos que isso: vazia, com ou sem links
CARACTERES_PAGINA_DE_CONTEUDO = 2500  # poucos links + muito texto = página de conteúdo legítima


@dataclass
class Link:
    url: str
    texto: str
    tag: str  # 'a' ou 'iframe'


@dataclass
class AnaliseHtml:
    titulo: str | None
    links: list[Link] = field(default_factory=list)
    caracteres_texto: int = 0

    @property
    def parece_vazia(self) -> bool:
        if self.caracteres_texto < MIN_CARACTERES_TEXTO:
            return True
        return len(self.links) < MIN_LINKS and self.caracteres_texto < CARACTERES_PAGINA_DE_CONTEUDO


def _texto_do_link(a) -> str:
    texto = a.get_text(" ", strip=True)
    if not texto:  # link só com imagem/ícone: usa os atributos acessíveis
        img = a.find("img")
        texto = a.get("title") or a.get("aria-label") or (img.get("alt") if img else "") or ""
    return " ".join(texto.split())[:300]


def analisar_html(conteudo: bytes, url_pagina: str) -> AnaliseHtml:
    # BeautifulSoup com lxml detecta a codificação a partir dos bytes (meta charset).
    soup = BeautifulSoup(conteudo, "lxml")

    base = soup.find("base", href=True)
    url_base = resolver(url_pagina, base["href"]) if base else None
    url_base = url_base or url_pagina

    titulo = soup.title.get_text(" ", strip=True) if soup.title else None

    links: list[Link] = []
    vistos: set[str] = set()
    for a in soup.find_all("a", href=True):
        url = resolver(url_base, a["href"])
        if url and url not in vistos:
            vistos.add(url)
            links.append(Link(url, _texto_do_link(a), "a"))
    # Portais costumam embutir o sistema do fornecedor em <iframe>.
    for quadro in soup.find_all(["iframe", "frame"], src=True):
        url = resolver(url_base, quadro["src"])
        if url and url not in vistos:
            vistos.add(url)
            links.append(Link(url, quadro.get("title") or "[iframe]", "iframe"))

    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    caracteres = len(" ".join(soup.get_text(" ").split()))

    return AnaliseHtml(titulo=titulo or None, links=links, caracteres_texto=caracteres)
