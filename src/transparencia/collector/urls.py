"""Normalização de URLs e definição de "domínio próprio" da entidade.

Normalizar evita visitar a mesma página duas vezes por diferenças irrelevantes
(maiúsculas no host, porta padrão, parâmetros de rastreamento, âncoras).
"""

from urllib.parse import urljoin, urlsplit, urlunsplit

# Parâmetros de campanha/rastreamento: não mudam o conteúdo da página.
PARAMETROS_RASTREIO = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
}

# Esquemas que não são páginas (o HTML que os contém já fica no snapshot).
ESQUEMAS_IGNORADOS = ("javascript:", "mailto:", "tel:", "data:", "whatsapp:")

# URLs maiores que isso são quase sempre armadilhas: um link de login que embute o
# próprio endereço em `?redirect=`, recodificado a cada nível (`%252525...`).
TAMANHO_MAXIMO_URL = 500

# Segundo nível brasileiro: "algo.com.br" é um domínio, não "com.br".
SEGUNDO_NIVEL_BR = ("com.br", "net.br", "org.br", "edu.br")


def _eh_rota_spa(fragmento: str) -> bool:
    """`#/despesas` é uma ROTA de aplicação de página única, não uma âncora."""
    return fragmento.startswith(("/", "!/"))


def normalizar(url: str) -> str | None:
    """Forma canônica da URL, ou None se não for uma página HTTP(S) válida."""
    if len(url) > TAMANHO_MAXIMO_URL or "%2525" in url:  # "%" codificado duas vezes ou mais
        return None
    try:
        partes = urlsplit(url.strip())
        porta = partes.port
    except ValueError:
        return None
    if partes.scheme.lower() not in ("http", "https") or not partes.hostname:
        return None

    host = partes.hostname.lower()
    netloc = host if porta in (None, 80, 443) else f"{host}:{porta}"
    # Filtra os parâmetros sem recodificar os demais (alguns servidores são sensíveis a isso).
    query = "&".join(
        p
        for p in partes.query.split("&")
        if p and p.split("=", 1)[0].lower() not in PARAMETROS_RASTREIO
    )
    fragmento = partes.fragment if _eh_rota_spa(partes.fragment) else ""
    return urlunsplit((partes.scheme.lower(), netloc, partes.path or "/", query, fragmento))


def resolver(url_base: str, href: str) -> str | None:
    """Converte um `href` (relativo ou absoluto) da página em URL normalizada."""
    href = (href or "").strip()
    if not href or href.lower().startswith(ESQUEMAS_IGNORADOS):
        return None
    if href.startswith("#") and not _eh_rota_spa(href[1:]):
        return None  # âncora na própria página
    return normalizar(urljoin(url_base, href))


def host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def caminho(url: str) -> str:
    """Tudo depois do host: `/despesas?ano=2026#/rota`.

    É o que se usa para achar temas na URL: o host (`transparencia.betha.cloud`)
    diria "transparência" para todas as páginas do portal e não distinguiria nada.
    """
    partes = urlsplit(url)
    return f"{partes.path} {partes.query} {partes.fragment}"


def dominio_base(nome_host: str) -> str:
    """Domínio "dono" do host, para decidir se um link é interno ou externo.

    - `portal.duquedecaxias.rj.gov.br` -> `duquedecaxias.rj.gov.br` (município.UF.gov.br)
    - `japeri.geosiap.net.br`          -> `geosiap.net.br`
    - `transparencia.prefeitura.rio`   -> `prefeitura.rio`

    Heurística suficiente para a PoC. Em escala, o correto é a Public Suffix List.
    """
    partes = nome_host.lower().split(".")
    if nome_host.endswith(".gov.br"):
        n = 4
    elif nome_host.endswith(SEGUNDO_NIVEL_BR):
        n = 3
    else:
        n = 2
    return ".".join(partes[-n:])
