"""Os dois modos de buscar uma URL: HTTP simples e navegador headless.

- `BuscadorHttp` (httpx): rápido e barato, mas não executa JavaScript.
- `BuscadorNavegador` (Playwright/Chromium): executa JavaScript como um usuário
  real veria; mais lento (~segundos por página). Só é usado quando a página
  veio "vazia" no HTTP, ou quando o host já mostrou que precisa dele.

Os dois devolvem o mesmo `Resposta`, então o resto do coletor não sabe (nem
precisa saber) qual foi usado; o modo fica registrado em `pages.modo_render`.

Descoberta por cliques: em aplicações JavaScript, muitos itens de menu são
elementos clicáveis SEM `href` (cartões, `<div>` com evento de clique). Nas
páginas indicadas pelo coletor, o navegador clica nos clicáveis cujo texto
passa no filtro (vocabulário dos critérios) e anota para onde cada um leva.
"""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import ClassVar, Protocol

import httpx


@dataclass
class Resposta:
    url: str
    modo: str  # 'http' | 'browser'
    url_final: str | None = None
    status: int | None = None
    content_type: str | None = None
    conteudo: bytes | None = None
    screenshot: bytes | None = None
    erro: str | None = None
    # (url, texto) descobertos clicando em elementos sem href (só no navegador)
    links_por_clique: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.erro is None and self.status is not None and self.status < 400

    @property
    def eh_html(self) -> bool:
        return "html" in (self.content_type or "")


FiltroTexto = Callable[[str], bool]


class Buscador(Protocol):
    def buscar(
        self, url: str, limite_bytes: int, filtro_cliques: FiltroTexto | None = None
    ) -> Resposta: ...


class BuscadorHttp:
    def __init__(self, cliente: httpx.Client):
        self.cliente = cliente

    def buscar(
        self, url: str, limite_bytes: int, filtro_cliques: FiltroTexto | None = None
    ) -> Resposta:
        try:
            # stream: lê aos poucos e para no limite, sem baixar um arquivo gigante inteiro.
            with self.cliente.stream("GET", url) as r:
                tipo = r.headers.get("content-type", "").split(";")[0].strip().lower() or None
                partes, total = [], 0
                for bloco in r.iter_bytes():
                    total += len(bloco)
                    if total > limite_bytes:
                        return Resposta(
                            url,
                            "http",
                            str(r.url),
                            r.status_code,
                            tipo,
                            erro=f"conteúdo maior que o limite de {limite_bytes:,} bytes",
                        )
                    partes.append(bloco)
                return Resposta(url, "http", str(r.url), r.status_code, tipo, b"".join(partes))
        except httpx.HTTPError as e:
            return Resposta(url, "http", erro=f"{type(e).__name__}: {e}"[:500])


# Marca os elementos clicáveis sem link e devolve seus textos. "Clicável" é heurística:
# cursor de mãozinha, papel de botão/link, onclick, tabindex ou classe de cartão/botão
# (frameworks como Vue tratam o clique em JavaScript e nem sempre mudam o cursor).
# Fica só o clicável mais externo de cada item (o cartão, não o título dentro dele).
JS_MARCAR_CLICAVEIS = r"""() => {
  const CLASSE = /(^|[\s_-])(card|btn|button|menu-item|item-menu|link)([\s_-]|$)/i;
  const PAPEL = /^(button|link|menuitem|tab)$/;
  const clicavel = (el) =>
    getComputedStyle(el).cursor === 'pointer' || PAPEL.test(el.getAttribute('role') || '') ||
    el.hasAttribute('onclick') || el.tabIndex >= 0 || CLASSE.test(el.className || '');
  const saida = [], marcados = [], vistos = new Set();
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('a[href]') || typeof el.className !== 'string' || !clicavel(el)) continue;
    const texto = (el.innerText || '').trim().replace(/\s+/g, ' ');
    if (!texto || texto.length > 150 || vistos.has(texto)) continue;
    if (marcados.some((m) => m.contains(el))) continue;
    vistos.add(texto);
    marcados.push(el);
    el.setAttribute('data-mt-clique', String(saida.length));
    saida.push(texto);
  }
  return saida;
}"""

# Clique disparado no próprio elemento: funciona mesmo com aviso de cookies por cima.
# Se o item tiver um botão dentro (cartão com "ACESSAR"), clica no botão: o evento de
# clique sobe do filho para o pai, nunca desce do pai para o filho.
JS_CLICAR = r"""i => {
  const item = document.querySelector(`[data-mt-clique="${i}"]`);
  (item.querySelector('button, [role=button], [onclick]') || item).click();
}"""


class BuscadorNavegador:
    """Abre o Chromium só na primeira vez que for necessário (é caro)."""

    VIEWPORT: ClassVar[dict[str, int]] = {"width": 1366, "height": 900}
    MAX_CLIQUES = 12  # cada clique custa ~2 s (clicar, esperar, voltar)

    def __init__(self, user_agent: str, timeout_s: float):
        self.user_agent = user_agent
        self.timeout_ms = timeout_s * 1000
        self._playwright = None
        self._navegador = None
        self._contexto = None

    def _iniciar(self) -> None:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._navegador = self._playwright.chromium.launch(headless=True)
        self._contexto = self._navegador.new_context(
            user_agent=self.user_agent, viewport=self.VIEWPORT, locale="pt-BR"
        )

    def _esperar_aplicacao(self, pagina) -> None:
        # Espera a aplicação terminar de buscar os dados (sem requisições por 0,5 s).
        # Sites com conexões permanentes nunca ficam "ociosos": aí seguimos assim mesmo.
        with suppress(Exception):
            pagina.wait_for_load_state("networkidle", timeout=self.timeout_ms / 2)

    def _descobrir_por_cliques(self, pagina, filtro: FiltroTexto) -> list[tuple[str, str]]:
        url_inicial = pagina.url
        alvos = [t for t in pagina.evaluate(JS_MARCAR_CLICAVEIS) if filtro(t)]
        descobertos: list[tuple[str, str]] = []
        for texto in alvos[: self.MAX_CLIQUES]:
            try:
                # A cada volta a página é redesenhada: marca de novo e acha o item pelo texto.
                if pagina.url != url_inicial:
                    pagina.goto(url_inicial, timeout=self.timeout_ms)
                    self._esperar_aplicacao(pagina)
                textos = pagina.evaluate(JS_MARCAR_CLICAVEIS)
                if texto not in textos:
                    continue
                pagina.evaluate(JS_CLICAR, textos.index(texto))
                pagina.wait_for_function("u => location.href !== u", arg=url_inicial, timeout=4000)
                descobertos.append((pagina.url, texto))
            except Exception:
                continue  # clique sem navegação (abre modal, nada acontece): ignora
        return descobertos

    def buscar(
        self, url: str, limite_bytes: int, filtro_cliques: FiltroTexto | None = None
    ) -> Resposta:
        if self._contexto is None:
            self._iniciar()
        pagina = self._contexto.new_page()
        try:
            resposta = pagina.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            self._esperar_aplicacao(pagina)
            html = pagina.content().encode("utf-8")
            if len(html) > limite_bytes:
                html = html[:limite_bytes]
            tipo = None
            if resposta is not None:
                tipo = resposta.headers.get("content-type", "").split(";")[0].strip().lower()
            final = Resposta(
                url,
                "browser",
                pagina.url,
                resposta.status if resposta else None,
                tipo or "text/html",
                html,
                screenshot=pagina.screenshot(type="png"),
            )
            if filtro_cliques is not None:
                final.links_por_clique = self._descobrir_por_cliques(pagina, filtro_cliques)
            return final
        except Exception as e:
            return Resposta(url, "browser", erro=f"{type(e).__name__}: {e}"[:500])
        finally:
            pagina.close()

    def fechar(self) -> None:
        if self._navegador is not None:
            self._navegador.close()
        if self._playwright is not None:
            self._playwright.stop()
