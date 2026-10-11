"""Gera o catálogo de critérios (YAML) a partir do checklist oficial (.xlsx).

Por que gerar em vez de digitar à mão?
- Rastreabilidade: o catálogo é derivado mecanicamente da fonte oficial.
- Atualização: se o órgão publicar um checklist novo, basta rodar de novo e
  revisar o diff no Git.
- Qualidade: o gerador detecta problemas na fonte (numeração faltando, etc.).

As decisões que NÃO estão na planilha (classificação essencial/obrigatório/
recomendado, aplicabilidade, critérios condicionais) ficam explícitas nas
constantes abaixo — é aqui que cada escolha fica visível e versionada.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from transparencia.catalog.schema import (
    Catalogo,
    Categoria,
    Classificacao,
    Criterio,
    Dimensao,
    Matriz,
)

# Pesos por classificação — mesmos valores usados pelo PNTP (Atricon/TCs):
# essencial 2, obrigatório 1,5, recomendado 1.
PESOS_CLASSIFICACAO = {
    Classificacao.ESSENCIAL: 2.0,
    Classificacao.OBRIGATORIO: 1.5,
    Classificacao.RECOMENDADO: 1.0,
}

# Classificação adotada neste projeto (a calibrar com a matriz oficial vigente do PNTP).
# Essenciais = núcleo mínimo sem o qual não há transparência: existência do
# portal e busca, execução de receita/despesa, pessoal, licitações/contratos,
# relatórios fiscais da LRF e canal de pedidos de informação (e-SIC).
ESSENCIAIS = {
    "1.1",
    "1.2",
    "1.3",
    "3.1",
    "4.1",
    "4.3",
    "5.1",
    "5.6",
    "7.1",
    "8.1",
    "12.1",
    "19.5",
    "20.1",
    "15.1",
    "15.2",
}
# Blocos/categorias de boas práticas → recomendados.
CATEGORIAS_RECOMENDADAS = {"17", "18", "21"}

# Aplicabilidade por categoria/critério. Padrão: matriz comum vale para todos.
APLICA_A_CATEGORIA = {
    "19": ["executivo:*"],
    "20": ["executivo:*"],
    "21": ["executivo:*"],
}
APLICA_A_CRITERIO = {
    # A própria planilha diz "(Executivo Municipal)" nesses itens.
    "21.1": ["executivo:municipal"],
    "21.3": ["executivo:municipal"],
}

CONDICIONAIS = {
    "3.8": "Só se aplica se houver transferências voluntárias recebidas no período.",
    "4.8": "Só se aplica se houver convênios/repasses concedidos no período.",
    "4.9": "Só se aplica se houver acordos sem transferência de recursos.",
    "11.4": "Só se aplica se houver obras paralisadas; declaração de inexistência atende.",
    "15.6": "Rol pode ser vazio; deve haver declaração de que não houve desclassificação.",
    "15.7": "Lista pode ser vazia; deve haver declaração de que não há informação classificada.",
}
CATEGORIAS_CONDICIONAIS = {
    "13": "Depende de a entidade receber/executar emendas parlamentares (ADPF 854).",
}

# Cabeçalhos de bloco (linhas só com texto na coluna A).
BLOCOS = {
    "MATRIZ COMUM": ("informacoes_prioritarias", Matriz.COMUM),
    "TRANSPARÊNCIA ATIVA": ("transparencia_ativa", Matriz.COMUM),
    "TRANSPARÊNCIA PASSIVA": ("transparencia_passiva", Matriz.COMUM),
    "BOAS PRÁTICAS": ("boas_praticas", Matriz.COMUM),
    "MATRIZ ESPECÍFICA: PODER EXECUTIVO": ("especifica_executivo", Matriz.ESPECIFICA_EXECUTIVO),
}

RE_CATEGORIA = re.compile(r"^(\d+)\.$")
RE_CRITERIO = re.compile(r"^(\d+)\.(\d+)$")


def _limpa(texto: object) -> str | None:
    if texto is None:
        return None
    # A planilha usa um "hífen invisível" (U+00AD) em "e\u00adSIC": vira hífen normal.
    s = str(texto).replace("e\u00adSIC", "e-SIC").replace("\u00ad", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def inferir_dimensao(descricao: str) -> Dimensao:
    d = descricao.lower()
    if d.startswith("ferramenta de pesquisa"):
        return Dimensao.FILTRO_PESQUISA
    if d.startswith("gravação de relatórios"):
        return Dimensao.GRAVACAO_RELATORIOS
    if "informações atualizadas" in d:
        return Dimensao.ATUALIDADE
    if "histórico das informações" in d:
        return Dimensao.SERIE_HISTORICA
    return Dimensao.DISPONIBILIDADE


def _classificacao(codigo: str, categoria: str) -> Classificacao:
    if codigo in ESSENCIAIS:
        return Classificacao.ESSENCIAL
    if categoria in CATEGORIAS_RECOMENDADAS:
        return Classificacao.RECOMENDADO
    return Classificacao.OBRIGATORIO


def _nome_categoria(nome: str) -> str:
    """'ADMINISTRAÇÃO DO PATRIMÔNIO PÚBLICO - IMÓVEIS' -> 'Administração do patrimônio...'"""
    siglas = {"SIC", "LGPD", "RREO", "RGF"}
    palavras = []
    for i, p in enumerate(nome.split(" ")):
        nucleo = p.strip("()-")
        if nucleo in siglas:
            palavras.append(p)
        elif nucleo.upper() == "E-SIC":
            palavras.append(p.replace(nucleo, "e-SIC"))
        else:
            palavras.append(p.capitalize() if i == 0 else p.lower())
    return " ".join(palavras)


@dataclass
class _Estado:
    bloco: str = "informacoes_prioritarias"
    matriz: Matriz = Matriz.COMUM
    categoria: str | None = None
    ultima_fundamentacao: str | None = None
    categorias: list[Categoria] = field(default_factory=list)
    criterios: list[Criterio] = field(default_factory=list)


def construir_catalogo(xlsx: Path, versao: str = "2026.10.0") -> Catalogo:
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    ws = wb["Quesitos"]
    est = _Estado()

    for linha in ws.iter_rows(values_only=True):
        item, criterio, fundamentacao = (_limpa(c) for c in linha[:3])
        if item is None or item == "ITEM":
            continue

        # 1) Cabeçalho de bloco (ex.: "TRANSPARÊNCIA ATIVA")
        if item in BLOCOS and criterio is None:
            est.bloco, est.matriz = BLOCOS[item]
            continue

        # 2) Categoria (ex.: "4." / "DESPESA")
        if m := RE_CATEGORIA.match(item):
            est.categoria = m.group(1)
            est.ultima_fundamentacao = None
            est.categorias.append(
                Categoria(
                    codigo=est.categoria,
                    nome=_nome_categoria(criterio or ""),
                    bloco=est.bloco,
                    matriz=est.matriz,
                )
            )
            continue

        # 3) Critério (ex.: "4.1")
        if RE_CRITERIO.match(item):
            if est.categoria is None or criterio is None:
                raise ValueError(f"linha inesperada: {linha}")
            # Na planilha, células de fundamentação vazias herdam a da linha de cima
            # (células mescladas visualmente). Registramos isso explicitamente.
            herdada = fundamentacao is None
            if fundamentacao:
                est.ultima_fundamentacao = fundamentacao
            cat = est.categoria
            nota = CONDICIONAIS.get(item) or CATEGORIAS_CONDICIONAIS.get(cat)
            est.criterios.append(
                Criterio(
                    codigo=item,
                    categoria=cat,
                    descricao=criterio,
                    fundamentacao=est.ultima_fundamentacao,
                    fundamentacao_herdada=herdada and est.ultima_fundamentacao is not None,
                    dimensao=inferir_dimensao(criterio),
                    classificacao=_classificacao(item, cat),
                    aplica_a=APLICA_A_CRITERIO.get(item) or APLICA_A_CATEGORIA.get(cat, ["*"]),
                    condicional=nota is not None,
                    nota=nota,
                )
            )
            continue

        raise ValueError(f"linha não reconhecida na planilha: {linha}")

    return Catalogo(
        versao=versao,
        fonte=xlsx.name,
        pesos_classificacao=PESOS_CLASSIFICACAO,
        categorias=est.categorias,
        criterios=est.criterios,
        observacoes_fonte=_auditar_fonte(est.criterios),
    )


def _auditar_fonte(criterios: list[Criterio]) -> list[str]:
    """Aponta inconsistências da planilha original (pensamento crítico sobre a fonte)."""
    obs: list[str] = []
    por_cat: dict[str, list[int]] = {}
    for c in criterios:
        cat, num = c.codigo.split(".")
        por_cat.setdefault(cat, []).append(int(num))
    for cat, nums in por_cat.items():
        faltando = sorted(set(range(1, max(nums) + 1)) - set(nums))
        if faltando:
            obs.append(
                f"Categoria {cat}: numeração sem o(s) item(ns) "
                + ", ".join(f"{cat}.{n}" for n in faltando)
            )
    for c in criterios:
        herdada = c.fundamentacao_herdada
        if not herdada and c.fundamentacao and "LC nº 101/20" in c.fundamentacao:
            obs.append(
                f"Critério {c.codigo}: fundamentação cita 'LC nº 101/20'; "
                "provavelmente 'LC nº 101/00' (Lei de Responsabilidade Fiscal)."
            )
    return obs
