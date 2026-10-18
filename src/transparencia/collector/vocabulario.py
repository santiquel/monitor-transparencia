"""Vocabulário que orienta a navegação (coleta focada).

Cada tema corresponde a um grupo de critérios da PoC. Um link cujo texto ou URL
contém termos de um tema é visitado antes dos demais. Assim, com um limite
de ~60 páginas, o coletor chega às páginas que a avaliação vai precisar.

Os termos são comparados sem acento e em minúsculas, como palavras inteiras
(`\\b`), para que "loa" não case com "download".
"""

import re
import unicodedata

TEMAS: dict[str, tuple[str, ...]] = {
    # 1.1, 1.2: porta de entrada do portal
    "transparencia": ("transparencia", "portal da transparencia"),
    # 4.1, 4.5, 4.6
    "despesa": (
        "despesa",
        "despesas",
        "empenho",
        "empenhos",
        "liquidacao",
        "pagamento",
        "pagamentos",
    ),
    # 5.1
    "pessoal": ("servidor", "servidores", "pessoal", "folha", "remuneracao", "recursos humanos"),
    # 7.1
    "licitacoes": (
        "licitacao",
        "licitacoes",
        "licitatorio",
        "licitatorios",
        "pregao",
        "pregoes",
        "compras",
        "contrato",
        "contratos",
    ),
    # 12.1, 19.5, 20.1, 20.2
    "gestao_fiscal": (
        "rgf",
        "rreo",
        "loa",
        "ldo",
        "ppa",
        "lrf",
        "gestao fiscal",
        "execucao orcamentaria",
        "lei orcamentaria",
        "relatorios fiscais",
        "responsabilidade fiscal",
        "planejamento",
        "prestacao de contas",
    ),
    # 15.2
    "esic": ("e-sic", "esic", "sic", "acesso a informacao", "lai", "pedido de informacao"),
    # 18.3, 4.5
    "dados_abertos": ("dados abertos", "api", "open data", "webservice"),
    # 18.1
    "lgpd": ("lgpd", "protecao de dados", "dados pessoais", "encarregado", "privacidade"),
    # 2.9
    "contato": ("fale conosco", "ouvidoria", "contato", "contatos"),
    # 16.2, 16.3, 16.4
    "acessibilidade": ("acessibilidade",),
}

# Extensões tratadas como documento/arquivo de dados (não têm links a seguir).
EXTENSOES_DOCUMENTO = (
    ".pdf",
    ".csv",
    ".json",
    ".xml",
    ".xls",
    ".xlsx",
    ".ods",
    ".odt",
    ".doc",
    ".docx",
    ".zip",
    ".txt",
)


def sem_acento(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _compilar(termos: tuple[str, ...]) -> re.Pattern[str]:
    alternativas = "|".join(re.escape(t) for t in sorted(termos, key=len, reverse=True))
    return re.compile(rf"\b(?:{alternativas})\b")


_PADROES = {tema: _compilar(termos) for tema, termos in TEMAS.items()}

# Tema de cada critério da PoC que depende de uma página temática. Usado para
# marcar a URL que um revisor indicou: a página entra na coleta já com o tema
# certo, sem depender do texto do link ou do título (que podem enganar, como a
# folha de pagamento da Betha que parecia "despesa").
TEMA_POR_CRITERIO = {
    "4.1": "despesa",
    "4.5": "despesa",
    "4.6": "despesa",
    "5.1": "pessoal",
    "7.1": "licitacoes",
    "12.1": "gestao_fiscal",
    "19.5": "gestao_fiscal",
    "20.1": "gestao_fiscal",
    "20.2": "gestao_fiscal",
    "15.2": "esic",
    "18.1": "lgpd",
    "18.3": "dados_abertos",
}
MARCA_INDICADA = "[indicada na revisão]"


def texto_link_indicado(codigo: str) -> str:
    """Texto de link sintético da página indicada: carrega a marca e o tema."""
    tema = TEMA_POR_CRITERIO.get(codigo)
    termo = TEMAS[tema][0] if tema else ""
    return f"{MARCA_INDICADA} critério {codigo} {termo}".strip()


def temas_de(*textos: str) -> set[str]:
    """Temas encontrados no texto do link e/ou na URL."""
    # Na URL, separadores ("/", "_", "-", ".") viram espaço: "/gestao-fiscal/" -> "gestao fiscal".
    alvo = " ".join(sem_acento(t) for t in textos if t)
    alvo = re.sub(r"[/_.?=&#%+]", " ", alvo).replace("-", " ").replace("e sic", "e-sic")
    return {tema for tema, padrao in _PADROES.items() if padrao.search(alvo)}


def eh_documento(url: str) -> bool:
    caminho = url.split("#", 1)[0].split("?", 1)[0].lower()
    return caminho.endswith(EXTENSOES_DOCUMENTO)


def vale_clicar(texto: str) -> bool:
    """Vale clicar num item sem link? Só se o texto tiver tema de conteúdo.

    "Transparência" é genérico demais e "acessibilidade" costuma ser só o botão de
    contraste/fonte, que não leva a outra página.
    """
    return bool(temas_de(texto) - {"transparencia", "acessibilidade"})
