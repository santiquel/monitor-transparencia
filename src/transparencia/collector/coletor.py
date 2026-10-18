"""Núcleo do coletor: fila com prioridade, regras de navegação e limites.

Este módulo não conhece o banco de dados: devolve `PaginaColetada` (e avisa a
cada página via `ao_coletar`). Quem grava no banco é `execucao.py`. Essa
separação permite testar a navegação inteira sem rede e sem PostgreSQL.

Regras de navegação (o que seguir a partir de uma página):
1. Respeita a profundidade máxima (cliques a partir das sementes).
2. Links do mesmo domínio da entidade: seguidos. Links "genéricos" (sem termo do
   vocabulário) só a partir das sementes ou de páginas temáticas.
3. Links para outro domínio (fornecedor, Fala.BR...): só se o texto/URL tiver
   termo do vocabulário, ou se for <iframe>, com no máximo N saltos externos.
4. Documentos (PDF, CSV...): só se o link ou a página de origem forem temáticos.
5. Limites separados para páginas e documentos.
"""

import heapq
import itertools
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit

from transparencia.collector.armazenamento import Armazenamento, extensao_para
from transparencia.collector.buscadores import Buscador, Resposta
from transparencia.collector.fornecedor import detectar_fornecedor, escolher_fornecedor
from transparencia.collector.html import AnaliseHtml, Link, analisar_html
from transparencia.collector.robots import PoliticaRobots
from transparencia.collector.urls import caminho, dominio_base, host, normalizar
from transparencia.collector.vocabulario import eh_documento, temas_de

# Temas cujos critérios se apoiam em documentos baixados (RGF/RREO/LOA; dados abertos).
TEMAS_DE_DOCUMENTO = {"gestao_fiscal", "dados_abertos"}

PRIORIDADE_SEMENTE = 1000
PRIORIDADE_DESCOBERTA = 500
ATRASO_MAXIMO_S = 10.0  # teto para o Crawl-delay pedido pelo site

# Redes sociais, mapas e vídeos: nunca são o portal e só gastariam o limite de páginas.
HOSTS_IGNORADOS = (
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "youtu.be",
    "linkedin.com",
    "tiktok.com",
    "whatsapp.com",
    "google.com",
    "googleapis.com",
    "gstatic.com",
    "flickr.com",
)


@dataclass
class ParametrosColeta:
    max_paginas: int = 60
    max_paginas_site: int = 15  # parte de max_paginas reservada ao site oficial
    max_documentos: int = 25
    profundidade_max: int = 3
    saltos_externos_max: int = 1
    timeout_s: float = 20.0
    intervalo_por_host_s: float = 1.0
    limite_bytes_html: int = 5_000_000
    limite_bytes_documento: int = 20_000_000
    usar_navegador: bool = True
    ignorar_robots: bool = False


@dataclass
class _Alvo:
    url: str
    profundidade: int
    prioridade: float
    papel: str | None = None  # 'site' | 'portal' | 'descoberta' (candidato a portal)
    ramo: str = "site"  # de qual semente a página descende: 'site' ou 'portal'
    url_origem: str | None = None
    texto_link: str | None = None
    temas: frozenset[str] = frozenset()
    saltos_externos: int = 0


@dataclass
class PaginaColetada:
    url: str
    profundidade: int
    papel: str | None
    url_origem: str | None
    texto_link: str | None
    temas: list[str]
    url_final: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    titulo: str | None = None
    modo_render: str | None = None
    tamanho_bytes: int | None = None
    content_hash: str | None = None
    storage_path: str | None = None
    screenshot_path: str | None = None
    fetched_at: datetime | None = None
    erro: str | None = None

    @property
    def ok(self) -> bool:
        return self.erro is None and self.http_status is not None and self.http_status < 400


@dataclass
class ResultadoColeta:
    paginas: list[PaginaColetada] = field(default_factory=list)
    portal_descoberto: str | None = None
    fornecedor: str | None = None
    hosts_com_navegador: set[str] = field(default_factory=set)
    avisos: list[str] = field(default_factory=list)

    @property
    def coletadas(self) -> int:
        return sum(p.ok for p in self.paginas)

    @property
    def com_erro(self) -> int:
        return sum(not p.ok for p in self.paginas)


class Coletor:
    def __init__(
        self,
        params: ParametrosColeta,
        http: Buscador,
        robots: PoliticaRobots,
        armazenamento: Armazenamento,
        navegador: Buscador | None = None,
        ao_coletar: Callable[[PaginaColetada], None] | None = None,
        relogio: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], None] = time.sleep,
    ):
        self.params = params
        self.http = http
        self.robots = robots
        self.armazenamento = armazenamento
        self.navegador = navegador if params.usar_navegador else None
        self.ao_coletar = ao_coletar
        self.relogio = relogio
        self.dormir = dormir

    # ------------------------------------------------------------------ fila

    def _enfileirar(self, alvo: _Alvo) -> None:
        if alvo.url in self._conhecidas:
            return
        self._conhecidas.add(alvo.url)
        # heapq tira o MENOR primeiro: prioridade negativa = maior prioridade sai antes.
        heapq.heappush(self._fila, (-alvo.prioridade, alvo.profundidade, next(self._seq), alvo))

    def _vale_seguir(self, link, temas, proprio, saltos, documento, prof, pai: _Alvo) -> bool:
        if not proprio:
            # Fora do domínio, "transparência" sozinho não basta: quase todo site público
            # linka o Radar da Transparência (Atricon), o portal da CGU etc.
            especificos = temas - {"transparencia"}
            if saltos > self.params.saltos_externos_max:
                return False
            if not (especificos or link.tag == "iframe"):
                return False
        if documento:
            # Só documentos que algum critério usa: relatórios fiscais e dados abertos.
            # Na 1ª coleta completa, o limite de PDFs foi gasto com "Atos de designação
            # de fiscal de contrato" (tema licitações) e nenhum RGF foi baixado.
            return bool((temas | pai.temas) & TEMAS_DE_DOCUMENTO)
        # Links genéricos só a partir das sementes ou de páginas temáticas.
        return bool(temas) or prof <= 1 or bool(pai.temas)

    def _filhos(self, pai: _Alvo, analise: AnaliseHtml) -> None:
        prof = pai.profundidade + 1
        if prof > self.params.profundidade_max:
            return
        for link in analise.links:
            h = host(link.url)
            if link.url in self._conhecidas or h.endswith(HOSTS_IGNORADOS):
                continue
            temas = temas_de(link.texto, caminho(link.url))
            proprio = dominio_base(h) in self._dominios_proprios
            saltos = 0 if proprio else pai.saltos_externos + 1
            documento = eh_documento(link.url)
            papel = None
            prioridade = len(temas) + 2 * ("transparencia" in temas) + (link.tag == "iframe")
            if documento and "gestao_fiscal" in temas:
                prioridade += 3  # RGF/RREO/LOA são os documentos que os critérios exigem

            # Sem portal cadastrado: um link "Transparência" no site é candidato a portal.
            candidato_portal = self._descobrindo and pai.papel == "site"
            if candidato_portal and "transparencia" in temas_de(link.texto):
                papel, prioridade, saltos = "descoberta", PRIORIDADE_DESCOBERTA, 0
            elif not self._vale_seguir(link, temas, proprio, saltos, documento, prof, pai):
                continue
            if not temas and pai.temas:
                prioridade = 0.5  # link genérico dentro de página temática ("Consultar", "Ver")

            self._enfileirar(
                _Alvo(
                    url=link.url,
                    profundidade=prof,
                    prioridade=prioridade,
                    papel=papel,
                    url_origem=pai.url,
                    texto_link=link.texto,
                    temas=frozenset(temas),
                    saltos_externos=saltos,
                    ramo="portal" if papel == "descoberta" else pai.ramo,
                )
            )

    # ------------------------------------------------------------ requisições

    def _esperar_vez(self, url: str) -> None:
        """Limite de taxa por host: no mínimo `intervalo` segundos entre requisições."""
        h = host(url)
        intervalo = max(self.params.intervalo_por_host_s, self.robots.atraso_minimo(url) or 0)
        intervalo = min(intervalo, ATRASO_MAXIMO_S)
        ultimo = self._ultimo_acesso.get(h)
        if ultimo is not None:
            espera = ultimo + intervalo - self.relogio()
            if espera > 0:
                self.dormir(espera)
        self._ultimo_acesso[h] = self.relogio()

    def _com_navegador(self, url: str, clicar: bool) -> Resposta | None:
        if self.navegador is None:
            return None
        self._esperar_vez(url)
        try:
            return self.navegador.buscar(url, self.params.limite_bytes_html, clicar)
        except Exception as e:  # navegador não instalado / não abriu: segue só com HTTP
            self._resultado.avisos.append(f"navegador indisponível: {type(e).__name__}: {e}")
            self.navegador = None
            return None

    def _buscar(
        self, url: str, documento: bool, clicar: bool
    ) -> tuple[Resposta, AnaliseHtml | None]:
        # Servidor que precisou de navegador uma vez vai direto para ele (poupa uma requisição).
        if not documento and urlsplit(url).netloc in self._resultado.hosts_com_navegador:
            r = self._com_navegador(url, clicar)
            if r is not None and r.ok:
                return r, analisar_html(r.conteudo, r.url_final or url)
            # Falhou no navegador (ex.: o link era um download de PDF): tenta por HTTP.

        self._esperar_vez(url)
        limite = self.params.limite_bytes_documento if documento else self.params.limite_bytes_html
        r = self.http.buscar(url, limite)
        analise = analisar_html(r.conteudo, r.url_final or url) if r.ok and r.eh_html else None

        # HTTP falhou (bloqueio de robôs, TLS) ou veio "vazio" (app JavaScript): tenta navegador.
        precisa = not documento and (not r.ok or (analise is not None and analise.parece_vazia))
        if precisa and (rn := self._com_navegador(url, clicar)) is not None and rn.ok:
            # Marca o servidor que ENTREGOU o conteúdo: um link do portal pode redirecionar
            # para o sistema do fornecedor, e é este que precisa de navegador, não o portal.
            self._resultado.hosts_com_navegador.add(urlsplit(rn.url_final or url).netloc)
            return rn, analisar_html(rn.conteudo, rn.url_final or url)
        return r, analise

    # ------------------------------------------------------------- visita

    def _visitar(self, alvo: _Alvo) -> PaginaColetada:
        pagina = PaginaColetada(
            url=alvo.url,
            profundidade=alvo.profundidade,
            papel=alvo.papel,
            url_origem=alvo.url_origem,
            texto_link=alvo.texto_link,
            temas=sorted(alvo.temas),
        )
        decisao = self.robots.avaliar(alvo.url)
        if not decisao.permitido:
            rotulo = (
                "host inalcançável" if decisao.host_inalcancavel else "bloqueado pelo robots.txt"
            )
            pagina.erro = f"{rotulo} ({decisao.motivo})"
            return pagina

        # No navegador, clica nos itens sem link (menus em JS) da página inicial do portal
        # e das páginas de gestão fiscal (na Betha, "LRF - RGF" também é item sem link).
        clicar = alvo.ramo == "portal" and (alvo.profundidade == 0 or "gestao_fiscal" in alvo.temas)
        r, analise = self._buscar(alvo.url, eh_documento(alvo.url), clicar)
        pagina.fetched_at = datetime.now(UTC)
        pagina.url_final = normalizar(r.url_final) if r.url_final else None
        pagina.http_status = r.status
        pagina.content_type = r.content_type
        pagina.modo_render = r.modo
        pagina.erro = r.erro or (f"HTTP {r.status}" if r.status and r.status >= 400 else None)
        if pagina.url_final:
            self._conhecidas.add(pagina.url_final)
            # Redirecionou para outro servidor: vale o robots.txt do destino.
            if urlsplit(pagina.url_final).netloc != urlsplit(alvo.url).netloc:
                destino = self.robots.avaliar(pagina.url_final)
                if not destino.permitido:
                    pagina.erro = f"redirecionado para destino bloqueado ({destino.motivo})"
                    return pagina

        if r.conteudo:
            pagina.tamanho_bytes = len(r.conteudo)
            ext = extensao_para(r.content_type, pagina.url_final or alvo.url)
            pagina.content_hash, pagina.storage_path = self.armazenamento.salvar(r.conteudo, ext)
        if r.screenshot:
            _, pagina.screenshot_path = self.armazenamento.salvar(r.screenshot, ".png")
        if analise is not None:
            pagina.titulo = analise.titulo
            for url_clique, texto in r.links_por_clique:
                if url_normalizada := normalizar(url_clique):
                    analise.links.append(Link(url_normalizada, texto, "clique"))

        if not pagina.ok:
            return pagina

        if alvo.ramo == "portal" and (
            f := detectar_fornecedor(pagina.url_final or alvo.url, r.conteudo)
        ):
            self._fornecedores[f] += 1
        if alvo.papel == "descoberta" and self._resultado.portal_descoberto is None:
            self._resultado.portal_descoberto = pagina.url_final or alvo.url
            self._dominios_proprios.add(dominio_base(host(self._resultado.portal_descoberto)))
            self._descobrindo = False

        # Mesmo conteúdo já visto nesta coleta (ex.: "/" e "/index.php"): não repete os links.
        if analise is not None and pagina.content_hash not in self._hashes_vistos:
            self._filhos(alvo, analise)
        if pagina.content_hash:
            self._hashes_vistos.add(pagina.content_hash)
        return pagina

    # ---------------------------------------------------------------- execução

    def _limite_paginas(self, ramo: str, url_portal: str | None) -> int:
        """Limite de páginas por ramo.

        O portal costuma ter muito mais links temáticos que o site e, numa fila única,
        consumiria tudo. Mas critérios como 1.2, 2.9, 16.4 e 18.1 olham o SITE: ele tem
        uma cota reservada. Sem portal conhecido, o site pode usar o limite inteiro.
        """
        # No máximo 1/4 do total, para limites pequenos não zerarem o portal.
        cota_site = min(self.params.max_paginas_site, max(1, self.params.max_paginas // 4))
        tem_portal = url_portal is not None or self._resultado.portal_descoberto is not None
        if ramo == "site":
            return cota_site if tem_portal else self.params.max_paginas
        return self.params.max_paginas - cota_site

    def coletar(
        self,
        url_site: str,
        url_portal: str | None = None,
        indicadas: list[tuple[str, str]] = (),
    ) -> ResultadoColeta:
        """`indicadas`: (url, texto_link) apontadas por revisores humanos. Entram como
        sementes, com o texto marcado (tema do critério), e contam no limite de páginas do
        ramo do domínio delas (portal, se não forem do site oficial)."""
        self._resultado = ResultadoColeta()
        self._fila: list = []
        self._seq = itertools.count()
        self._conhecidas: set[str] = set()
        self._hashes_vistos: set[str] = set()
        self._ultimo_acesso: dict[str, float] = {}
        self._fornecedores: Counter[str] = Counter()
        self._descobrindo = url_portal is None

        sementes = [(url_portal, "portal"), (url_site, "site")]
        self._dominios_proprios = {dominio_base(host(u)) for u, _ in sementes if u}
        for url, papel in sementes:
            if url and (normalizada := normalizar(url)):
                self._enfileirar(_Alvo(normalizada, 0, PRIORIDADE_SEMENTE, papel=papel, ramo=papel))
        # Mesmo HOST do site oficial = ramo do site; o resto (portal, sistemas de
        # fornecedores) usa o limite do portal. Comparar o domínio-base erraria:
        # site e portal costumam compartilhá-lo (www.x.rj.gov.br, transparencia.x.rj.gov.br).
        host_site = host(url_site)
        for url, texto in indicadas:
            if normalizada := normalizar(url):
                ramo = "site" if host(normalizada) == host_site else "portal"
                self._enfileirar(
                    _Alvo(normalizada, 1, PRIORIDADE_SEMENTE, ramo=ramo, texto_link=texto)
                )

        paginas = {"site": 0, "portal": 0}
        documentos = 0
        while self._fila:
            *_, alvo = heapq.heappop(self._fila)
            documento = eh_documento(alvo.url)
            if documento and documentos >= self.params.max_documentos:
                continue
            if not documento and paginas[alvo.ramo] >= self._limite_paginas(alvo.ramo, url_portal):
                continue

            pagina = self._visitar(alvo)
            if pagina.fetched_at is None:
                pass  # bloqueada antes de qualquer requisição: não conta no limite
            elif "html" in (pagina.content_type or "html"):
                paginas[alvo.ramo] += 1
            else:
                documentos += 1
            self._resultado.paginas.append(pagina)
            if self.ao_coletar:
                self.ao_coletar(pagina)

        self._resultado.fornecedor = escolher_fornecedor(self._fornecedores)
        if self._descobrindo:
            self._resultado.avisos.append("portal não encontrado a partir do site oficial")
        return self._resultado
