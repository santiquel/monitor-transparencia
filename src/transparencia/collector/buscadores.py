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
tem vocabulário dos critérios (`vale_clicar`) e anota para onde cada um leva.
"""

import multiprocessing
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import ClassVar, Protocol

import httpx

from transparencia.collector.vocabulario import vale_clicar


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


class Buscador(Protocol):
    def buscar(self, url: str, limite_bytes: int, clicar: bool = False) -> Resposta: ...


class BuscadorHttp:
    def __init__(self, cliente: httpx.Client):
        self.cliente = cliente

    def buscar(self, url: str, limite_bytes: int, clicar: bool = False) -> Resposta:
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
# Um <a href="#"> (ou vazio, ou "javascript:") NÃO é link de verdade: o destino é tratado
# em JavaScript (caso do portal de Duque de Caxias, com 50 deles). Esses entram como clicáveis.
JS_MARCAR_CLICAVEIS = r"""() => {
  const CLASSE = /(^|[\s_-])(card|btn|button|menu-item|item-menu|link)([\s_-]|$)/i;
  const PAPEL = /^(button|link|menuitem|tab)$/;
  const LINK_REAL = 'a[href]:not([href="#"]):not([href=""]):not([href^="javascript"])';
  const clicavel = (el) =>
    el.tagName === 'A' || getComputedStyle(el).cursor === 'pointer' ||
    PAPEL.test(el.getAttribute('role') || '') || el.hasAttribute('onclick') ||
    el.tabIndex >= 0 || CLASSE.test(el.className || '');
  const saida = [], marcados = [], vistos = new Set();
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest(LINK_REAL) || typeof el.className !== 'string' || !clicavel(el)) continue;
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


PRAZO_CLIQUES_S = 60  # tempo total para a descoberta por cliques numa página


class _NavegadorLocal:
    """O Playwright de fato. Roda DENTRO do processo filho (ver `BuscadorNavegador`)."""

    VIEWPORT: ClassVar[dict[str, int]] = {"width": 1366, "height": 900}
    MAX_CLIQUES = 12  # cada clique custa ~2 s (clicar, esperar, voltar)

    def __init__(self, user_agent: str, timeout_s: float):
        self.user_agent = user_agent
        self.timeout_ms = timeout_s * 1000
        self._playwright = None
        self._navegador = None
        self._contexto = None

    def iniciar(self) -> None:
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

    def _html(self, pagina) -> bytes:
        """Conteúdo renderizado. Páginas que redirecionam em JS podem estar "no meio" de
        uma navegação: espera um pouco e tenta de novo, até 3 vezes."""
        for tentativa in range(3):
            try:
                return pagina.content().encode("utf-8")
            except Exception:
                if tentativa == 2:
                    raise
                pagina.wait_for_timeout(1000)
        return b""

    def _descobrir_por_cliques(self, pagina) -> list[tuple[str, str]]:
        url_inicial = pagina.url
        prazo = time.monotonic() + PRAZO_CLIQUES_S
        alvos = [t for t in pagina.evaluate(JS_MARCAR_CLICAVEIS) if vale_clicar(t)]
        descobertos: list[tuple[str, str]] = []
        for texto in alvos[: self.MAX_CLIQUES]:
            if time.monotonic() > prazo:
                break
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

    def buscar(self, url: str, limite_bytes: int, clicar: bool = False) -> Resposta:
        if self._contexto is None:
            self.iniciar()
        pagina = self._contexto.new_page()
        pagina.set_default_timeout(self.timeout_ms)
        try:
            resposta = pagina.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            self._esperar_aplicacao(pagina)
            html = self._html(pagina)[:limite_bytes]
            tipo = None
            if resposta is not None:
                tipo = resposta.headers.get("content-type", "").split(";")[0].strip().lower()
            screenshot = None
            with suppress(Exception):  # a captura é evidência útil, mas opcional
                screenshot = pagina.screenshot(type="png", timeout=10_000)
            final = Resposta(
                url,
                "browser",
                pagina.url,
                resposta.status if resposta else None,
                tipo or "text/html",
                html,
                screenshot=screenshot,
            )
            if clicar:
                final.links_por_clique = self._descobrir_por_cliques(pagina)
            return final
        except Exception as e:
            return Resposta(url, "browser", erro=f"{type(e).__name__}: {e}"[:500])
        finally:
            with suppress(Exception):
                pagina.close()

    def fechar(self) -> None:
        with suppress(Exception):
            if self._navegador is not None:
                self._navegador.close()
            if self._playwright is not None:
                self._playwright.stop()


def _trabalhador(conexao, user_agent: str, timeout_s: float) -> None:
    """Laço do processo filho: recebe (url, limite, clicar), devolve uma `Resposta`."""
    navegador = _NavegadorLocal(user_agent, timeout_s)
    try:
        navegador.iniciar()
    except Exception as e:  # Chromium não instalado, por exemplo: avisa o processo pai
        conexao.send(RuntimeError(f"{type(e).__name__}: {e}"))
        return
    conexao.send("pronto")
    try:
        while (pedido := conexao.recv()) is not None:
            conexao.send(navegador.buscar(*pedido))
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        navegador.fechar()


class BuscadorNavegador:
    """Navegador num PROCESSO FILHO, vigiado por um prazo.

    Algumas páginas travam o Chromium (ex.: formulários Webrun de Duque de Caxias,
    que redirecionam em loop): a captura de tela estoura o tempo e nem fechar a aba
    funciona mais. Timeouts dentro do Playwright não resolvem um navegador travado.
    Por isso o navegador roda em outro processo: se não responder dentro do prazo,
    o processo é encerrado à força, a página fica registrada com o erro, o coletor
    segue com o conteúdo do HTTP simples e um navegador novo é aberto no próximo uso.

    Em escala (5.000 portais), isso é o que impede uma página defeituosa de parar
    a fila inteira.
    """

    def __init__(self, user_agent: str, timeout_s: float, alvo=_trabalhador):
        self.user_agent = user_agent
        self.timeout_s = timeout_s
        self._alvo = alvo  # trocável nos testes
        self._processo = None
        self._conexao = None

    def _iniciar(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        self._conexao, filho = ctx.Pipe()
        self._processo = ctx.Process(
            target=self._alvo, args=(filho, self.user_agent, self.timeout_s), daemon=True
        )
        self._processo.start()
        filho.close()
        if not self._conexao.poll(60):
            self._encerrar()
            raise RuntimeError("navegador não iniciou em 60 s")
        aviso = self._conexao.recv()
        if isinstance(aviso, Exception):
            self._encerrar()
            raise aviso

    def _encerrar(self) -> None:
        if self._processo is not None and self._processo.is_alive():
            self._processo.kill()
            self._processo.join(5)
        self._processo = None

    def prazo(self, clicar: bool) -> float:
        """goto + espera + captura cabem em ~3x o timeout; cliques têm prazo próprio."""
        return self.timeout_s * 3 + (PRAZO_CLIQUES_S + 30 if clicar else 0)

    def buscar(self, url: str, limite_bytes: int, clicar: bool = False) -> Resposta:
        if self._processo is None or not self._processo.is_alive():
            self._iniciar()
        self._conexao.send((url, limite_bytes, clicar))
        prazo = self.prazo(clicar)
        if self._conexao.poll(prazo):
            with suppress(EOFError):
                return self._conexao.recv()
        self._encerrar()
        return Resposta(
            url,
            "browser",
            erro=f"navegador não respondeu em {prazo:.0f} s (a página travou o navegador); "
            "processo reiniciado",
        )

    def fechar(self) -> None:
        if self._processo is not None and self._processo.is_alive():
            with suppress(Exception):
                self._conexao.send(None)
            self._processo.join(15)
        self._encerrar()
