"""O que o coletor viu, pronto para os avaliadores consultarem.

Lê as linhas de `pages` de uma coleta e, sob demanda, o arquivo de cada página
em `data/`. A avaliação nunca acessa a internet: trabalha só sobre isto.
"""

import re
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from pathlib import Path

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from sqlalchemy import select
from sqlalchemy.orm import Session

from transparencia.collector.urls import caminho, normalizar
from transparencia.collector.vocabulario import MARCA_INDICADA, sem_acento, temas_de
from transparencia.db.models import CrawlRun, Page

# Páginas XHTML (sistemas Java/JSF) são lidas como HTML de propósito.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


@dataclass
class PaginaSnapshot:
    page: Page
    data_dir: Path

    @property
    def id(self) -> int:
        return self.page.id

    @property
    def url(self) -> str:
        return self.page.url_final or self.page.url

    @property
    def ok(self) -> bool:
        p = self.page
        return p.erro is None and p.http_status is not None and p.http_status < 400

    @property
    def eh_html(self) -> bool:
        return "html" in (self.page.content_type or "")

    @cached_property
    def conteudo(self) -> bytes:
        if not self.page.storage_path:
            return b""
        caminho_arquivo = self.data_dir / self.page.storage_path
        return caminho_arquivo.read_bytes() if caminho_arquivo.exists() else b""

    @cached_property
    def soup(self) -> BeautifulSoup:
        return BeautifulSoup(self.conteudo if self.eh_html else b"", "lxml")

    @cached_property
    def texto(self) -> str:
        """Texto visível, com espaços normalizados (é onde se procuram as citações)."""
        if not self.eh_html:
            return ""
        copia = BeautifulSoup(self.conteudo, "lxml")
        for tag in copia(["script", "style", "noscript", "template"]):
            tag.decompose()
        return " ".join(copia.get_text(" ").split())

    @cached_property
    def texto_busca(self) -> str:
        """Texto sem acento e em minúsculas, para comparar com padrões."""
        return sem_acento(self.texto)

    @property
    def indicada(self) -> bool:
        """Página apontada por um revisor humano como a certa para um critério."""
        return (self.page.texto_link or "").startswith(MARCA_INDICADA)

    @cached_property
    def temas(self) -> set[str]:
        """Temas da página: pelo link que levou a ela, pela URL e pelo título."""
        p = self.page
        return temas_de(p.texto_link or "", caminho(self.url), p.titulo or "")


@dataclass
class Snapshot:
    crawl_run: CrawlRun
    url_site: str | None
    url_portal: str | None
    paginas: list[PaginaSnapshot] = field(default_factory=list)

    @property
    def data_coleta(self) -> datetime:
        return self.crawl_run.started_at

    @property
    def ok(self) -> list[PaginaSnapshot]:
        return [p for p in self.paginas if p.ok]

    @property
    def html(self) -> list[PaginaSnapshot]:
        return [p for p in self.ok if p.eh_html]

    @property
    def orcamento_esgotado(self) -> bool:
        """A coleta parou por orçamento (e não por falta de links)?

        Importa para concluir AUSÊNCIA: se o coletor parou antes de ver tudo,
        "não encontrei" não prova "não existe".
        """
        params = self.crawl_run.parametros or {}
        maximo = params.get("max_paginas", 60)
        paginas_html = sum(1 for p in self.paginas if p.page.fetched_at and p.eh_html)
        return paginas_html >= 0.9 * maximo

    def _inicial(self, url: str | None) -> PaginaSnapshot | None:
        alvo = normalizar(url) if url else None
        for p in self.paginas:
            if p.page.profundidade == 0 and alvo in (p.page.url, p.page.url_final):
                return p
        return None

    @property
    def site_inicial(self) -> PaginaSnapshot | None:
        return self._inicial(self.url_site)

    @property
    def portal_inicial(self) -> PaginaSnapshot | None:
        return self._inicial(self.url_portal)

    def com_tema(self, *temas: str) -> list[PaginaSnapshot]:
        """Páginas do tema; as INDICADAS por revisores humanos vêm primeiro."""
        paginas = [p for p in self.html if p.temas & set(temas)]
        return sorted(paginas, key=lambda p: not p.indicada)  # sort estável: mantém a ordem

    def buscar(self, padrao: re.Pattern[str], paginas=None) -> list[tuple[PaginaSnapshot, str]]:
        """Páginas cujo texto casa com o padrão, com o trecho literal ao redor."""
        achados = []
        for p in self.html if paginas is None else paginas:
            m = padrao.search(p.texto_busca)
            if m:
                achados.append((p, trecho(p.texto, m.start(), m.end())))
        return achados


def trecho(texto: str, inicio: int, fim: int, margem: int = 80) -> str:
    """Recorte literal do texto original ao redor de um achado.

    `texto_busca` (sem acento) tem o mesmo tamanho que `texto` para caracteres
    latinos comuns, então as posições valem para os dois.
    """
    return texto[max(0, inicio - margem) : fim + margem].strip()


def carregar_snapshot(session: Session, crawl_run: CrawlRun, data_dir: Path) -> Snapshot:
    pages = session.scalars(
        select(Page).where(Page.crawl_run_id == crawl_run.id).order_by(Page.id)
    ).all()
    params = crawl_run.parametros or {}
    url_site = params.get("url_site")
    url_portal = crawl_run.url_inicial if crawl_run.url_inicial != url_site else None
    return Snapshot(
        crawl_run=crawl_run,
        url_site=url_site,
        url_portal=url_portal,
        paginas=[PaginaSnapshot(p, data_dir) for p in pages],
    )
