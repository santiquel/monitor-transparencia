"""Extratores de sinais reutilizados pelos avaliadores.

Cada função responde a uma pergunta pequena sobre uma página ("há um link
com este texto?", "que datas aparecem nas tabelas?") e devolve também ONDE
achou (elemento, seletor CSS, trecho), para virar evidência.
"""

import re
from dataclasses import dataclass
from datetime import date

from bs4 import Tag

from transparencia.collector.vocabulario import sem_acento
from transparencia.evaluation.snapshot import PaginaSnapshot
from transparencia.evaluation.veredito import Evidencia

ATRIBUTOS_DESCRITIVOS = ("title", "aria-label", "alt", "placeholder", "value", "name", "id")


def padrao(*termos: str) -> re.Pattern[str]:
    """Regex de palavras inteiras, para comparar com texto sem acento."""
    alternativas = "|".join(sorted(termos, key=len, reverse=True))
    return re.compile(rf"\b(?:{alternativas})\b")


def evidencia(
    p: PaginaSnapshot, tipo: str, descricao: str, trecho=None, seletor=None, **meta
) -> Evidencia:
    return Evidencia(
        tipo=tipo,
        descricao=descricao,
        url=p.url,
        page_id=p.id,
        titulo_pagina=p.page.titulo,
        trecho=trecho,
        seletor=seletor,
        arquivo_path=p.page.screenshot_path if tipo == "screenshot" else p.page.storage_path,
        metadados=meta or None,
    )


def seletor_css(tag: Tag, niveis: int = 4) -> str:
    """Caminho CSS curto até o elemento (ex.: `header > nav.menu > a`), para auditoria."""
    partes = []
    atual: Tag | None = tag
    while atual is not None and atual.name not in ("[document]", "html", "body") and niveis:
        parte = atual.name
        if atual.get("id"):
            partes.append(f"{parte}#{atual['id']}")
            break
        classes = [c for c in atual.get("class", []) if c][:2]
        if classes:
            parte += "." + ".".join(classes)
        partes.append(parte)
        atual = atual.parent
        niveis -= 1
    return " > ".join(reversed(partes))


def descricao_elemento(tag: Tag) -> str:
    """Tudo o que descreve o elemento: texto, atributos acessíveis, classes, href/src."""
    pedacos = [tag.get_text(" ", strip=True)[:200]]
    pedacos += [str(tag.get(a, "")) for a in ATRIBUTOS_DESCRITIVOS]
    pedacos.append(" ".join(tag.get("class", [])))
    pedacos += [str(tag.get("href", "")), str(tag.get("src", ""))]
    img = tag.find("img") if tag.name != "img" else None
    if img is not None:
        pedacos += [str(img.get("alt", "")), str(img.get("src", ""))]
    return sem_acento(" ".join(pedacos)).replace("-", " ").replace("_", " ")


def elementos(
    p: PaginaSnapshot, regex: re.Pattern[str], tags=("a", "button", "img", "span", "i", "div", "li")
) -> list[Tag]:
    """Elementos (pequenos) cuja descrição casa com o padrão."""
    achados = []
    for tag in p.soup.find_all(tags):
        # Elementos grandes (um <div> com a página inteira) casariam com tudo: ignora.
        if len(tag.get_text(" ", strip=True)) > 200:
            continue
        if regex.search(descricao_elemento(tag)):
            achados.append(tag)
    return achados


# Classes que as bibliotecas de menu usam para o PAINEL ESCONDIDO (que só abre ao
# passar o mouse/clicar). Comparadas como nome INTEIRO de classe, não como pedaço:
# o Wix chama o menu principal inteiro de "wixui-dropdown-menu" (itens visíveis), e
# no Bootstrap "li.dropdown" é o botão visível; só "ul/div.dropdown-menu" esconde.
CLASSES_PAINEL_ESCONDIDO = frozenset(
    {
        "sub-menu",
        "submenu",
        "dropdown-menu",
        "dropdown-content",
        "children",
        "collapse",
        "mega-menu",
        "megamenu",
    }
)


def posicao(tag: Tag) -> str:
    """Onde o elemento está: 'rodape', 'submenu' ou 'visivel'.

    Heurística pela estrutura do HTML:
    - dentro de <footer> (ou classe/id com "footer"/"rodape") = rodapé;
    - dentro de uma lista ANINHADA em outro item de menu (<ul> dentro de <li>),
      ou de um painel com classe de menu escondido (CLASSES_PAINEL_ESCONDIDO)
      = só aparece ao abrir o menu;
    - o resto = visível.
    """
    for pai in tag.parents:
        if not isinstance(pai, Tag) or pai.name in ("body", "html"):
            break
        rotulo = sem_acento(" ".join(pai.get("class", [])) + " " + str(pai.get("id", "")))
        if pai.name == "footer" or re.search(r"footer|rodape", rotulo):
            return "rodape"
        classes = {c.lower() for c in pai.get("class", [])}
        if classes & CLASSES_PAINEL_ESCONDIDO:
            return "submenu"
        if pai.name in ("ul", "ol") and pai.find_parent("li") is not None:
            return "submenu"
    return "visivel"


# ------------------------------------------------------------------ datas

RE_DATA = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b")
RE_ANO = re.compile(r"\b(20[0-4]\d)\b")


def datas(texto: str) -> list[date]:
    encontradas = []
    for d, m, a in RE_DATA.findall(texto):
        try:
            encontradas.append(date(int(a), int(m), int(d)))
        except ValueError:
            continue  # 31/02/2026 e afins
    return encontradas


def datas_em_tabelas(p: PaginaSnapshot) -> list[date]:
    """Datas dentro de células de tabela (ou grades de dados), não no cabeçalho do site.

    Muitos portais mostram "hoje é 14/10/2026" no topo: essa data não diz nada
    sobre a atualização dos dados, por isso só valem datas em células.
    """
    celulas = p.soup.find_all(["td"]) or p.soup.select('[role="gridcell"], [role="cell"]')
    return [d for c in celulas for d in datas(c.get_text(" ", strip=True))]


def anos(texto: str) -> set[int]:
    return {int(a) for a in RE_ANO.findall(texto)}


# --------------------------------------------------------------- formatos

FORMATOS = {
    "csv": r"\bcsv\b",
    "xls": r"\bxlsx?\b|\bexcel\b",
    "ods": r"\bods\b|\bcalc\b",
    "json": r"\bjson\b",
    "xml": r"\bxml\b",
    "pdf": r"\bpdf\b",
    "txt": r"\btxt\b",
    "doc": r"\bdocx?\b|\bodt\b|\bword\b",
}
LEGIVEIS_POR_MAQUINA = {"csv", "xls", "ods", "json", "xml"}


@dataclass
class Exportacao:
    formato: str
    tag: Tag


def exportacoes(p: PaginaSnapshot) -> list[Exportacao]:
    """Botões/links de exportação e os formatos que oferecem.

    Ícones (<img>, <i>, <span>) só contam dentro de um link ou botão: um ícone
    solto não exporta nada. Exemplos de código (<pre>, <code>) da documentação
    de uma API também não contam ("representante_doc" não é botão de DOC).
    """
    achados = []
    for tag in p.soup.find_all(["a", "button", "input", "img", "i", "span"]):
        if len(tag.get_text(" ", strip=True)) > 60 or tag.find_parent(["pre", "code", "script"]):
            continue
        if tag.name in ("img", "i", "span") and tag.find_parent(["a", "button"]) is None:
            continue
        desc = descricao_elemento(tag)
        for formato, regex in FORMATOS.items():
            if re.search(regex, desc):
                achados.append(Exportacao(formato, tag))
    return achados


# ----------------------------------------------------------------- tabelas


def cabecalhos(p: PaginaSnapshot) -> list[tuple[Tag, str]]:
    """Cabeçalhos de cada tabela (ou grade), já sem acento: [(tabela, 'nome cargo ...')]."""
    resultado = []
    for tabela in p.soup.find_all("table"):
        ths = tabela.find_all("th") or (
            tabela.find("tr").find_all("td") if tabela.find("tr") else []
        )
        texto = " | ".join(th.get_text(" ", strip=True) for th in ths)
        if texto:
            resultado.append((tabela, sem_acento(texto)))
    for grade in p.soup.select('[role="grid"], [role="table"]'):
        cols = grade.select('[role="columnheader"]')
        texto = " | ".join(c.get_text(" ", strip=True) for c in cols)
        if texto:
            resultado.append((grade, sem_acento(texto)))
    return resultado


def linhas_de_dados(tabela: Tag) -> int:
    return (
        len(tabela.find_all("tr")) - 1
        if tabela.name == "table"
        else len(tabela.select('[role="row"]'))
    )
