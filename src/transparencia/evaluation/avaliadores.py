"""Avaliadores determinísticos dos 19 critérios da PoC.

Cada função recebe o snapshot de uma coleta e devolve um `Veredito` com
evidências. As decisões seguem a rubrica de `config/evaluation_rules.yaml`.

Princípios:
- **Ausência exige busca suficiente.** "Não encontrei" só vira NAO_ATENDE se a
  coleta não parou por orçamento; senão é NAO_AVALIADO (ver `_ausencia`).
- **Página certa, dado invisível = zona cinzenta.** Ex.: a página de despesas
  existe, mas os dados só aparecem depois de preencher um formulário. Isso não
  prova que o portal falha: vira NAO_AVALIADO com revisão humana (ou IA, 3.3).
- **Evidência positiva basta; negativa exige fonte completa.** Achar um dado
  recente prova atualização; achar só dados antigos NÃO prova atraso, porque o
  snapshot vê parte do portal (o resto fica atrás de consultas). Afirmações
  negativas sobre qualidade (sem exportação, desatualizado, bimestres em atraso)
  só viram NAO_ATENDE com uma fonte que declara o todo; senão, NAO_AVALIADO com
  revisão. As 3 acusações falsas da 1ª medição contra o golden set vinham daí.
- **Confiança é declarada, não calculada por mágica:** cada regra diz o quanto
  confia no próprio sinal (ex.: link "Transparência" no topo = 0,8, porque a
  "visibilidade" é inferida da estrutura do HTML, não vista na tela).
"""

import re
from collections.abc import Callable
from datetime import date, timedelta

from transparencia.collector.urls import dominio_base, host, resolver
from transparencia.collector.vocabulario import sem_acento
from transparencia.db.models import StatusConformidade as S
from transparencia.evaluation import sinais
from transparencia.evaluation.sinais import evidencia, padrao, seletor_css
from transparencia.evaluation.snapshot import PaginaSnapshot, Snapshot, trecho
from transparencia.evaluation.veredito import Veredito, nao_avaliado

Avaliador = Callable[[Snapshot], Veredito]
AVALIADORES: dict[str, Avaliador] = {}


def avaliador(codigo: str):
    def registrar(funcao: Avaliador) -> Avaliador:
        AVALIADORES[codigo] = funcao
        return funcao

    return registrar


# ----------------------------------------------------------------- apoio


def _paginas_iniciais(snap: Snapshot) -> list[PaginaSnapshot]:
    return [p for p in (snap.site_inicial, snap.portal_inicial) if p is not None and p.ok]


def _ausencia(snap: Snapshot, oque: str, procurou_em: list[PaginaSnapshot] | None = None):
    """'Não encontrei X': conclusivo só se a coleta não parou por orçamento."""
    procurou_em = procurou_em or _paginas_iniciais(snap)
    evidencias = [
        evidencia(p, "metadado", f"Procurado nesta página: {oque}") for p in procurou_em[:3]
    ]
    total = len(snap.html)
    if snap.orcamento_esgotado:
        return nao_avaliado(
            f"{oque} não encontrado nas {total} páginas coletadas, mas a coleta parou "
            "por orçamento: a ausência não está comprovada.",
            evidencias,
            paginas_analisadas=total,
        )
    return Veredito(
        S.NAO_ATENDE,
        0.6,
        f"{oque} não encontrado nas {total} páginas coletadas (a coleta percorreu "
        "todos os links relevantes dentro dos limites de profundidade).",
        evidencias,
        detalhes={"paginas_analisadas": total},
    )


def _dados_invisiveis(p: PaginaSnapshot, oque: str) -> Veredito:
    return nao_avaliado(
        f"Página de {oque} encontrada, mas os dados não estão visíveis no snapshot "
        "(provavelmente exigem filtro/consulta). Requer verificação humana.",
        [evidencia(p, "metadado", f"Página de {oque} sem dados visíveis")],
    )


# ============================================================ existência


@avaliador("1.1")
def site_ou_portal_acessivel(snap: Snapshot) -> Veredito:
    candidatas = [p for p in (snap.site_inicial, snap.portal_inicial) if p is not None]
    ok = [p for p in candidatas if p.ok]
    if ok:
        nomes = ", ".join(p.url for p in ok)
        return Veredito(
            S.ATENDE,
            0.95,
            f"Acessível(is) na coleta: {nomes}.",
            [
                evidencia(
                    p,
                    "screenshot" if p.page.screenshot_path else "metadado",
                    f"Página inicial respondeu HTTP {p.page.http_status}",
                )
                for p in ok
            ],
        )
    if not candidatas:
        return nao_avaliado("Páginas iniciais do site e do portal não foram registradas na coleta.")
    erros = "; ".join(f"{p.page.url}: {p.page.erro}" for p in candidatas)
    if all("robots.txt" in (p.page.erro or "") for p in candidatas):
        return nao_avaliado(f"Coleta bloqueada pelo robots.txt ({erros}).")
    return Veredito(
        S.NAO_ATENDE,
        0.6,  # pode ser instabilidade momentânea: por isso pede revisão
        f"Nem o site nem o portal responderam na coleta ({erros}).",
        [evidencia(p, "metadado", p.page.erro or "sem resposta") for p in candidatas],
    )


RE_TRANSPARENCIA = padrao("transparencia")


@avaliador("1.2")
def link_portal_na_capa(snap: Snapshot) -> Veredito:
    p = snap.site_inicial
    if p is None or not p.ok:
        return nao_avaliado("Página inicial do site oficial não foi coletada.")
    host_portal = host(snap.url_portal) if snap.url_portal else None

    links = []
    for a in p.soup.find_all("a"):
        destino = resolver(p.url, a.get("href", "")) or ""
        aponta_portal = host_portal is not None and host(destino) == host_portal
        if RE_TRANSPARENCIA.search(sinais.descricao_elemento(a)) or aponta_portal:
            links.append((a, sinais.posicao(a)))
    if not links:
        return Veredito(
            S.NAO_ATENDE,
            0.8,
            "Nenhum link com 'Transparência' ou apontando para o portal na página inicial do site.",
            [
                evidencia(
                    p,
                    "screenshot" if p.page.screenshot_path else "metadado",
                    "Página inicial do site analisada",
                )
            ],
        )

    visiveis = [(a, pos) for a, pos in links if pos == "visivel"]
    escolhidos = visiveis or links
    evs = [
        evidencia(
            p,
            "link",
            f"Link '{a.get_text(' ', strip=True)[:60]}' ({pos})",
            trecho=str(a)[:300],
            seletor=seletor_css(a),
            href=a.get("href"),
        )
        for a, pos in escolhidos[:3]
    ]
    if p.page.screenshot_path:
        evs.append(evidencia(p, "screenshot", "Captura da página inicial do site"))
    if visiveis:
        return Veredito(S.ATENDE, 0.8, "Link para o portal visível na página inicial do site.", evs)
    onde = sorted({pos for _, pos in links})
    return Veredito(
        S.ATENDE_PARCIALMENTE,
        0.7,
        f"Link para o portal só aparece em: {', '.join(onde)}.",
        evs,
    )


RE_BUSCA = padrao("pesquisar", "pesquisa", "buscar", "busca", "procurar", "search")
NOMES_CAMPO_BUSCA = {"q", "s", "busca", "pesquisa", "search", "termo", "keyword", "query"}


@avaliador("1.3")
def ferramenta_de_pesquisa(snap: Snapshot) -> Veredito:
    iniciais = _paginas_iniciais(snap)
    if not iniciais:
        return nao_avaliado("Páginas iniciais do site e do portal não foram coletadas.")
    fracos = []
    for p in iniciais:
        campos = p.soup.select('input[type="search"], form[role="search"], [role="search"]')
        campos += [
            i
            for i in p.soup.find_all("input")
            if str(i.get("name", "")).lower() in NOMES_CAMPO_BUSCA
            or RE_BUSCA.search(sinais.descricao_elemento(i))
        ]
        if campos:
            c = campos[0]
            return Veredito(
                S.ATENDE,
                0.9,
                "Campo de pesquisa encontrado.",
                [
                    evidencia(
                        p,
                        "formulario",
                        "Campo de pesquisa",
                        trecho=str(c)[:300],
                        seletor=seletor_css(c),
                    )
                ],
            )
        fracos += [(p, t) for t in sinais.elementos(p, RE_BUSCA, tags=("a", "button"))]
    if fracos:
        p, t = fracos[0]
        return Veredito(
            S.ATENDE,
            0.7,
            "Botão/link de pesquisa encontrado (campo provavelmente abre ao clicar).",
            [
                evidencia(
                    p, "elemento", "Acesso à pesquisa", trecho=str(t)[:300], seletor=seletor_css(t)
                )
            ],
        )
    return Veredito(
        S.NAO_ATENDE,
        0.75,
        "Nenhum campo ou botão de pesquisa nas páginas iniciais.",
        [evidencia(p, "metadado", "Página inicial analisada") for p in iniciais],
    )


RE_CONTATO = padrao("fale conosco", "ouvidoria", "contato", "contatos", "atendimento ao cidadao")
RE_TELEFONE = re.compile(r"\(?\b\d{2}\)?\s?\d{4,5}[-\s]?\d{4}\b")
RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


@avaliador("2.9")
def fale_conosco(snap: Snapshot) -> Veredito:
    canais, meios = [], []
    for p in snap.html:
        canais += [(p, t) for t in sinais.elementos(p, RE_CONTATO, tags=("a", "button"))]
        meio = p.soup.select_one('a[href^="mailto:"], a[href^="tel:"]')
        if meio is not None:
            meios.append((p, meio))
        elif m := RE_TELEFONE.search(p.texto) or RE_EMAIL.search(p.texto):
            meios.append((p, m.group(0)))
    if canais:
        p, t = canais[0]
        evs = [
            evidencia(p, "link", "Canal de contato", trecho=str(t)[:300], seletor=seletor_css(t))
        ]
        if meios:
            pm, m = meios[0]
            evs.append(
                evidencia(pm, "texto", "Meio de contato (e-mail/telefone)", trecho=str(m)[:200])
            )
            return Veredito(S.ATENDE, 0.85, "Canal de contato com e-mail/telefone.", evs)
        return Veredito(S.ATENDE, 0.7, "Link para canal de contato (Fale Conosco/Ouvidoria).", evs)
    return _ausencia(snap, "canal 'Fale Conosco'/Ouvidoria")


# ========================================================== acessibilidade


RE_CONTRASTE = re.compile(r"contraste|contrast")
RE_FONTE = re.compile(
    r"(aumentar|diminuir|reduzir|ampliar)( o| a)? (tamanho (da |do )?)?(fonte|letra|texto)"
    r"|tamanho d[ao] (fonte|texto|letra)|font ?size|fonte (maior|menor)"
)
RE_SIMBOLO = re.compile(r"acessibilidade|accessibility|vlibras|\blibras\b")


def _botoes_de_fonte(p: PaginaSnapshot) -> list:
    """Botões "A+"/"A-" (ou "+"/"-" ao lado de "Acessibilidade"/"Fonte")."""
    achados = sinais.elementos(p, RE_FONTE, tags=("a", "button", "li", "span"))
    for tag in p.soup.find_all(["a", "button", "li", "span"]):
        texto = tag.get_text("", strip=True)
        if re.fullmatch(r"[+-]?\s?A\s?[+-]|[+-]\s?A", texto):
            achados.append(tag)
        elif texto in ("+", "-") and tag.parent is not None:
            vizinhanca = sem_acento(tag.parent.get_text(" ", strip=True))
            if re.search(r"acessibilidade|fonte|\ba\b", vizinhanca):
                achados.append(tag)
    return achados


def _recurso_de_acessibilidade(snap: Snapshot, buscar, oque: str) -> Veredito:
    iniciais = _paginas_iniciais(snap)
    if not iniciais:
        return nao_avaliado("Páginas iniciais do site e do portal não foram coletadas.")
    for p in iniciais + [x for x in snap.html if x not in iniciais]:
        achados = buscar(p)
        if achados:
            t = achados[0]
            return Veredito(
                S.ATENDE,
                0.85,
                f"{oque} encontrado.",
                [evidencia(p, "elemento", oque, trecho=str(t)[:300], seletor=seletor_css(t))],
            )
    return Veredito(
        S.NAO_ATENDE,
        0.75,
        f"{oque} não encontrado no site nem no portal.",
        [evidencia(p, "metadado", "Página analisada") for p in iniciais],
    )


@avaliador("16.2")
def alto_contraste(snap: Snapshot) -> Veredito:
    return _recurso_de_acessibilidade(
        snap,
        lambda p: sinais.elementos(p, RE_CONTRASTE, tags=("a", "button", "li", "span", "img", "i")),
        "Opção de alto contraste",
    )


@avaliador("16.3")
def redimensionar_texto(snap: Snapshot) -> Veredito:
    return _recurso_de_acessibilidade(snap, _botoes_de_fonte, "Ferramenta de tamanho de fonte")


def _simbolo(p: PaginaSnapshot) -> list:
    vlibras = p.soup.select("[vw], .vw-plugin-wrapper, script[src*='vlibras']")
    return vlibras + sinais.elementos(p, RE_SIMBOLO, tags=("a", "button", "img", "i", "span"))


@avaliador("16.4")
def simbolo_acessibilidade(snap: Snapshot) -> Veredito:
    paginas = {"site": snap.site_inicial, "portal": snap.portal_inicial}
    faltando = [nome for nome, p in paginas.items() if p is None or not p.ok]
    com, sem, evs = [], [], []
    for nome, p in paginas.items():
        if nome in faltando:
            continue
        achados = _simbolo(p)
        if achados:
            com.append(nome)
            evs.append(
                evidencia(
                    p,
                    "elemento",
                    f"Símbolo de acessibilidade no {nome}",
                    trecho=str(achados[0])[:300],
                    seletor=seletor_css(achados[0]),
                )
            )
        else:
            sem.append(nome)
            evs.append(evidencia(p, "metadado", f"Sem símbolo de acessibilidade no {nome}"))
    if faltando:
        return nao_avaliado(
            f"Página inicial não coletada: {', '.join(faltando)}. O critério exige site E portal.",
            evs,
            com_simbolo=com,
        )
    if len(com) == 2:
        return Veredito(S.ATENDE, 0.85, "Símbolo de acessibilidade no site e no portal.", evs)
    if com:
        return Veredito(
            S.ATENDE_PARCIALMENTE, 0.8, f"Símbolo só no {com[0]} (falta no {sem[0]}).", evs
        )
    return Veredito(S.NAO_ATENDE, 0.75, "Sem símbolo de acessibilidade no site e no portal.", evs)


# ========================================================= exige navegação


def _campos(texto: str, campos: dict[str, str]) -> set[str]:
    return {nome for nome, rx in campos.items() if re.search(rx, texto)}


def _melhor_tabela(paginas: list[PaginaSnapshot], campos: dict[str, str]):
    """(página, tabela, campos encontrados) da tabela com mais campos esperados."""
    melhor = (None, None, set())
    for p in paginas:
        for tabela, cab in sinais.cabecalhos(p):
            achados = _campos(cab, campos)
            if len(achados) > len(melhor[2]):
                melhor = (p, tabela, achados)
    return melhor


CAMPOS_DESPESA = {
    "empenhado": r"empenhad|valor (do )?empenho",
    "liquidado": r"liquidad",
    "pago": r"\bpag[oa]s?\b|valor pago",
}
RE_NUMERO_EMPENHO = re.compile(
    r"\b(n[o.]?|numero|num\.?|nr\.?)\s*(do |de )?empenho|\bempenho\s*(n[o.]?|numero)|\b20\d{2}ne\d+"
)


@avaliador("4.1")
def empenho_liquidacao_pagamento(snap: Snapshot) -> Veredito:
    paginas = snap.com_tema("despesa")
    if not paginas:
        return _ausencia(snap, "Consulta de despesas")
    p, tabela, fases = _melhor_tabela(paginas, CAMPOS_DESPESA)
    if p is None:
        return _dados_invisiveis(paginas[0], "despesas")
    cab = sinais.cabecalhos(p)
    tem_numero = bool(RE_NUMERO_EMPENHO.search(" ".join(c for _, c in cab) + " " + p.texto_busca))
    ev = evidencia(
        p,
        "tabela",
        f"Tabela de despesas com: {', '.join(sorted(fases))}",
        trecho=" | ".join(th.get_text(" ", strip=True) for th in tabela.find_all("th"))[:300],
        seletor=seletor_css(tabela),
        numero_empenho=tem_numero,
    )
    if fases == set(CAMPOS_DESPESA) and tem_numero:
        return Veredito(
            S.ATENDE,
            0.8,
            "Despesas com número do empenho e valores empenhado, liquidado e pago.",
            [ev],
        )
    faltam = sorted(set(CAMPOS_DESPESA) - fases) + ([] if tem_numero else ["número do empenho"])
    return Veredito(
        S.ATENDE_PARCIALMENTE, 0.65, f"Consulta de despesas sem: {', '.join(faltam)}.", [ev]
    )


CAMPOS_SERVIDORES = {
    "nome": r"\bnome\b",
    "cargo": r"cargo|funcao|emprego",
    "vinculo": r"vinculo|regime|matricula|lotacao|situacao",
}


@avaliador("5.1")
def relacao_nominal_servidores(snap: Snapshot) -> Veredito:
    paginas = snap.com_tema("pessoal")
    if not paginas:
        return _ausencia(snap, "Lista de servidores")
    p, tabela, campos = _melhor_tabela(paginas, CAMPOS_SERVIDORES)
    if p is None or not campos:
        return _dados_invisiveis(paginas[0], "servidores")
    ev = evidencia(
        p,
        "tabela",
        f"Tabela de servidores com: {', '.join(sorted(campos))}",
        seletor=seletor_css(tabela),
        linhas=sinais.linhas_de_dados(tabela),
    )
    if "nome" in campos and campos - {"nome"}:
        return Veredito(S.ATENDE, 0.85, "Lista de servidores com nome e cargo/vínculo.", [ev])
    if "nome" in campos:
        return Veredito(S.ATENDE, 0.7, "Lista de servidores com nome.", [ev])
    return Veredito(S.ATENDE_PARCIALMENTE, 0.7, "Lista de servidores sem o nome.", [ev])


CAMPOS_LICITACAO = {
    "numero": r"\bn[o.]?\b|numero|processo|edital",
    "modalidade": r"modalidade",
    "objeto": r"objeto|descricao",
    "data": r"\bdata\b|abertura|publicacao",
    "valor": r"valor",
    "situacao": r"situacao|status|fase|andamento",
    "vencedor": r"vencedor|fornecedor|contratad|adjudicad|homologad|empresa",
}


@avaliador("7.1")
def relacao_licitacoes(snap: Snapshot) -> Veredito:
    paginas = snap.com_tema("licitacoes")
    if not paginas:
        return _ausencia(snap, "Lista de licitações")
    p, tabela, campos = _melhor_tabela(paginas, CAMPOS_LICITACAO)
    if p is None or len(campos) < 3:
        return _dados_invisiveis(paginas[0], "licitações")
    ev = evidencia(
        p,
        "tabela",
        f"Tabela de licitações com: {', '.join(sorted(campos))}",
        seletor=seletor_css(tabela),
        campos=sorted(campos),
    )
    if campos == set(CAMPOS_LICITACAO):  # a rubrica pede todos, inclusive o vencedor
        return Veredito(S.ATENDE, 0.75, "Lista de licitações com os 7 campos.", [ev])
    faltam = sorted(set(CAMPOS_LICITACAO) - campos)
    return Veredito(
        S.ATENDE_PARCIALMENTE, 0.7, f"Lista de licitações sem: {', '.join(faltam)}.", [ev]
    )


# ===================================================== dados estruturados


@avaliador("4.5")
def exportacao_despesa(snap: Snapshot) -> Veredito:
    paginas = snap.com_tema("despesa")
    if not paginas:
        return _ausencia(snap, "Consulta de despesas")
    exportacoes = [(p, e) for p in paginas for e in sinais.exportacoes(p)]
    formatos = {e.formato for _, e in exportacoes}
    evs = [
        evidencia(
            p,
            "elemento",
            f"Exportação em {e.formato.upper()}",
            trecho=str(e.tag)[:200],
            seletor=seletor_css(e.tag),
        )
        for p, e in {e.formato: (p, e) for p, e in exportacoes}.values()
    ]
    abertos = formatos & sinais.LEGIVEIS_POR_MAQUINA
    if len(formatos) >= 2 and abertos:
        return Veredito(S.ATENDE, 0.8, f"Exportação em {', '.join(sorted(formatos))}.", evs)
    if formatos:
        # "Só um formato" também é afirmação negativa sobre o que não se viu: revisão.
        return Veredito(
            S.ATENDE_PARCIALMENTE, 0.6, f"Exportação só em {', '.join(sorted(formatos))}.", evs
        )
    return nao_avaliado(
        f"Nenhuma opção de exportação nas {len(paginas)} páginas de despesa coletadas. "
        "Os botões de exportação costumam aparecer só depois da consulta: a ausência não "
        "está comprovada.",
        [evidencia(p, "metadado", "Página de despesa sem exportação") for p in paginas[:3]],
    )


RE_REGRAS_USO = padrao(
    "termos de uso",
    "termo de uso",
    "licenca",
    "licenca de uso",
    "politica de dados abertos",
    "regras de uso",
    "condicoes de uso",
    "creative commons",
    "cc by",
    "odbl",
    "licenca aberta",
)
RE_API = padrao("api", "webservice", "web service", "swagger", "endpoint")


@avaliador("18.3")
def dados_abertos(snap: Snapshot) -> Veredito:
    paginas = snap.com_tema("dados_abertos")
    if not paginas:
        return _ausencia(snap, "Seção de dados abertos")
    dados = [
        (p, e.tag, e.formato)
        for p in paginas
        for e in sinais.exportacoes(p)
        if e.formato in sinais.LEGIVEIS_POR_MAQUINA
    ]
    dados += [(p, None, "api") for p in paginas if RE_API.search(p.texto_busca)]
    regras = snap.buscar(RE_REGRAS_USO, paginas)
    evs = []
    if dados:
        p, tag, formato = dados[0]
        evs.append(
            evidencia(
                p,
                "arquivo_dados",
                f"Dados abertos ({formato})",
                trecho=str(tag)[:200] if tag else None,
            )
        )
    if regras:
        p, t = regras[0]
        evs.append(evidencia(p, "texto", "Regras de uso dos dados", trecho=t))
    if dados and regras:
        return Veredito(
            S.ATENDE, 0.75, "Dados legíveis por máquina e regras de uso publicadas.", evs
        )
    if dados or regras:
        falta = "regras de uso" if dados else "dados legíveis por máquina"
        return Veredito(S.ATENDE_PARCIALMENTE, 0.65, f"Seção de dados abertos sem {falta}.", evs)
    return Veredito(
        S.NAO_ATENDE,
        0.6,
        "Seção de dados abertos sem dados legíveis por máquina nem regras de uso.",
        [evidencia(p, "metadado", "Página de dados abertos analisada") for p in paginas[:3]],
    )


# ============================================================ documentos


def _documentos(
    snap: Snapshot, regex: re.Pattern[str]
) -> list[tuple[PaginaSnapshot, str, set[int]]]:
    """Menções a um documento: em links, em linhas de listas/tabelas e em arquivos baixados.

    Devolve (página, texto da menção, anos citados nela). O ano vem do ELEMENTO
    que contém a menção (a linha `tr` ou o item `li`), nunca de uma janela de
    texto: numa lista "LOA 2025 / RREO 2026", a janela daria 2026 para a LOA.
    Funciona também em listas montadas por JavaScript, que nem sempre têm <a href>.
    """
    itens = []
    for p in snap.html:
        for tag in p.soup.find_all(True):
            texto = " ".join(tag.get_text(" ", strip=True).split())
            if not texto or len(texto) > 300:
                continue
            href = str(tag.get("href", ""))
            if not regex.search(sem_acento(f"{texto} {href}")):
                continue
            # Só o elemento mais interno que menciona o documento...
            if any(
                regex.search(sem_acento(f.get_text(" ", strip=True))) for f in tag.find_all(True)
            ):
                continue
            # ...mas lido dentro da sua linha/item, onde costuma estar o ano.
            linha = tag.find_parent(["tr", "li"])
            if linha is not None and len(linha.get_text(" ", strip=True)) <= 300:
                texto = " ".join(linha.get_text(" ", strip=True).split())
            itens.append((p, texto[:300], sinais.anos(sem_acento(f"{texto} {href}"))))
    for p in snap.ok:
        if not p.eh_html:
            desc = sem_acento((p.page.texto_link or "") + " " + p.url)
            if regex.search(desc):
                itens.append((p, p.page.texto_link or p.url, sinais.anos(desc)))
    return itens


def _publicacao(snap: Snapshot, regex, nome: str, ano_minimo: int) -> Veredito:
    itens = _documentos(snap, regex)
    if not itens:
        return _ausencia(snap, nome, snap.com_tema("gestao_fiscal") or None)
    com_ano = [(p, t, max(a)) for p, t, a in itens if a]
    if not com_ano:
        p, t, _ = itens[0]
        return nao_avaliado(
            f"{nome} encontrado, mas sem ano identificável.",
            [evidencia(p, "documento", nome, trecho=t)],
        )
    p, t, ano = max(com_ano, key=lambda x: x[2])
    ev = evidencia(p, "documento", f"{nome} mais recente encontrado: {ano}", trecho=t, ano=ano)
    if ano >= ano_minimo:
        return Veredito(S.ATENDE, 0.8, f"{nome} de {ano} publicado.", [ev])
    return Veredito(S.ATENDE_PARCIALMENTE, 0.75, f"{nome} mais recente é de {ano}.", [ev])


RE_RGF = re.compile(r"\brgf\b|relatorio d[ae] gestao fiscal")
RE_RREO = re.compile(r"\brreo\b|relatorio resumido d[ae] execucao orcamentaria")
RE_LOA = re.compile(r"\bloa\b|lei orcamentaria anual")


@avaliador("12.1")
def rgf(snap: Snapshot) -> Veredito:
    return _publicacao(snap, RE_RGF, "RGF", snap.data_coleta.year - 1)


@avaliador("20.1")
def rreo(snap: Snapshot) -> Veredito:
    return _publicacao(snap, RE_RREO, "RREO", snap.data_coleta.year - 1)


@avaliador("19.5")
def loa(snap: Snapshot) -> Veredito:
    return _publicacao(snap, RE_LOA, "LOA", snap.data_coleta.year)


# =========================================================== atualização


RE_ATUALIZADO = re.compile(r"atualiza\w*\s*(?:em|:)?\s*:?\s*(\d{1,2}/\d{1,2}/20\d{2})")
# "Publicado em 19/08/2019 | Atualizado em 30/04/2025" é metadado do CMS (WordPress):
# a data de edição do TEXTO da página, não dos dados (achado: 4.6 do Rio).
RE_PUBLICADO = re.compile(r"publicad[oa]\s*(?:em|:)")


def _datas_declaradas(p: PaginaSnapshot) -> list[date]:
    datas = []
    for m in RE_ATUALIZADO.finditer(p.texto_busca):
        if RE_PUBLICADO.search(p.texto_busca[max(0, m.start() - 60) : m.start()]):
            continue
        datas += sinais.datas(m.group(1))
    return datas


@avaliador("4.6")
def despesa_atualizada(snap: Snapshot) -> Veredito:
    paginas = snap.com_tema("despesa")
    if not paginas:
        return _ausencia(snap, "Consulta de despesas")
    hoje = snap.data_coleta.date()
    candidatas = []
    for p in paginas:
        candidatas += [(d, p, "célula de tabela") for d in sinais.datas_em_tabelas(p)]
        candidatas += [(d, p, "'atualizado em'") for d in _datas_declaradas(p)]
    validas = [c for c in candidatas if date(2000, 1, 1) <= c[0] <= hoje]
    if not validas:
        return _dados_invisiveis(paginas[0], "despesas (sem datas visíveis)")
    d, p, origem = max(validas, key=lambda c: c[0])
    dias = (hoje - d).days
    declarada = origem == "'atualizado em'"  # o próprio portal declara a data
    ev = evidencia(
        p,
        "texto",
        f"Data mais recente ({origem}): {d:%d/%m/%Y}, {dias} dias antes da coleta",
        trecho=d.strftime("%d/%m/%Y"),
        dias=dias,
    )
    if dias <= 30:
        return Veredito(S.ATENDE, 0.75, f"Dado de despesa de {dias} dias antes da coleta.", [ev])
    if dias <= 90:
        return Veredito(
            S.ATENDE_PARCIALMENTE,
            0.7 if declarada else 0.6,
            f"Dado de despesa mais recente: {dias} dias.",
            [ev],
        )
    if declarada:
        return Veredito(S.NAO_ATENDE, 0.7, f"Portal declara atualização há {dias} dias.", [ev])
    return nao_avaliado(
        f"A data mais recente vista em tabelas de despesa é de {dias} dias atrás, mas "
        "datas antigas no snapshot não provam atraso: os dados atuais podem estar atrás "
        "de uma consulta não capturada.",
        [ev],
    )


RE_BIMESTRE = re.compile(r"\b([1-6])\s*[o.]?\s*bim")


def bimestre_exigivel(dia: date) -> tuple[int, int]:
    """(ano, bimestre) do último RREO vencido: vence 30 dias após o fim do bimestre."""
    ano, bim = dia.year, 6
    while True:
        fim_mes = 2 * bim  # bimestre 4 termina em agosto (mês 8)
        fim = date(ano + (fim_mes == 12), 1 if fim_mes == 12 else fim_mes + 1, 1) - timedelta(
            days=1
        )
        if fim + timedelta(days=30) <= dia:
            return ano, bim
        bim -= 1
        if bim == 0:
            ano, bim = ano - 1, 6


@avaliador("20.2")
def rreo_atualizado(snap: Snapshot) -> Veredito:
    itens = _documentos(snap, RE_RREO)
    if not itens:
        return _ausencia(snap, "RREO", snap.com_tema("gestao_fiscal") or None)
    periodos = []
    for p, texto, anos_item in itens:
        m = RE_BIMESTRE.search(sem_acento(texto))
        if m and anos_item:
            periodos.append(((max(anos_item), int(m.group(1))), p, texto))
    if not periodos:
        p, t, _ = itens[0]
        return nao_avaliado(
            "RREO encontrado, mas sem bimestre identificável.",
            [evidencia(p, "documento", "RREO", trecho=t)],
        )
    (ano, bim), p, texto = max(periodos, key=lambda x: x[0])
    exig_ano, exig_bim = bimestre_exigivel(snap.data_coleta.date())
    atraso = (exig_ano * 6 + exig_bim) - (ano * 6 + bim)
    ev = evidencia(
        p,
        "documento",
        f"RREO mais recente: {bim}º bimestre de {ano}",
        trecho=texto,
        exigivel=f"{exig_bim}º bimestre de {exig_ano}",
        atraso_bimestres=atraso,
    )
    if atraso <= 0:
        return Veredito(
            S.ATENDE, 0.8, f"RREO do {bim}º bim/{ano} publicado (exigível: {exig_bim}º).", [ev]
        )
    if atraso == 1:
        return Veredito(
            S.ATENDE_PARCIALMENTE, 0.6, f"RREO com 1 bimestre de atraso ({bim}º/{ano}).", [ev]
        )
    # Listas de relatórios costumam ser consultas filtradas por exercício ou paginadas
    # (ex.: Betha mostrava só 2025). O mais recente VISTO não prova que não há outro.
    return nao_avaliado(
        f"O RREO mais recente visto no snapshot é o {bim}º bimestre de {ano} ({atraso} "
        "bimestres antes do exigível), mas a lista pode estar filtrada por exercício ou "
        "paginada: o atraso não está comprovado.",
        [ev],
    )


# ================================================= interpretação semântica
# Regras determinísticas resolvem os casos claros; o resto é zona cinzenta,
# encaminhada à IA na etapa 3.3 (aqui, NAO_AVALIADO com revisão humana).


RE_EXIGENCIA_ESIC = re.compile(
    r"copia (do |de |da )?(documento|rg|cpf|identidade)|anexar (o |um )?documento"
    r"|documento de identidade|assinatura (reconhecida|digital)|reconhecimento de firma"
    r"|firma reconhecida|declaracao de responsabilidade|maior de (18|idade)|maioridade"
)


@avaliador("15.2")
def esic_simples(snap: Snapshot) -> Veredito:
    paginas = snap.com_tema("esic") + [p for p in snap.html if "falabr.cgu.gov.br" in p.url]
    if not paginas:
        return _ausencia(snap, "e-SIC")
    if exigencias := snap.buscar(RE_EXIGENCIA_ESIC, paginas):
        p, t = exigencias[0]
        return Veredito(
            S.NAO_ATENDE,
            0.7,
            "O e-SIC menciona exigência que dificulta o acesso.",
            [evidencia(p, "texto", "Exigência no pedido de informação", trecho=t)],
        )
    falabr = [p for p in paginas if "falabr.cgu.gov.br" in p.url]
    if falabr:
        # Conhecimento de fornecedor: o Fala.BR (CGU) pede só login gov.br.
        return Veredito(
            S.ATENDE,
            0.85,
            "e-SIC pelo Fala.BR (CGU), que exige apenas cadastro/login.",
            [evidencia(falabr[0], "link", "e-SIC no Fala.BR")],
            detalhes={"regra": "fornecedor Fala.BR"},
        )
    return nao_avaliado(
        "Página do e-SIC encontrada, sem exigências explícitas no texto; a decisão "
        "depende de interpretar o formulário (zona cinzenta).",
        [evidencia(p, "metadado", "Página do e-SIC analisada") for p in paginas[:3]],
        zona_cinzenta=True,
    )


RE_ENCARREGADO = re.compile(r"encarregad[oa]|\bdpo\b|data protection officer")


# "Encarregado" também é cargo (encarregado de obras, de turma): a menção só conta
# perto de vocabulário de proteção de dados.
RE_CONTEXTO_LGPD = re.compile(r"dados pessoais|protecao de dados|\blgpd\b|privacidade|tratamento")


def _da_entidade(snap: Snapshot, p: PaginaSnapshot) -> bool:
    """A página é da própria entidade (domínio do site ou host do portal)?

    Exclui, por exemplo, a política de privacidade do FORNECEDOR do portal
    (betha.com.br), que fala do encarregado da empresa, não da prefeitura.
    """
    hosts = {host(u) for u in (snap.url_site, snap.url_portal) if u}
    bases = {dominio_base(h) for h in hosts if h.endswith(".gov.br")}
    h = host(p.url)
    return h in hosts or dominio_base(h) in bases


def _mencoes_encarregado(snap: Snapshot) -> list[tuple[PaginaSnapshot, re.Match[str]]]:
    mencoes = []
    for p in snap.html:
        if not _da_entidade(snap, p):
            continue
        for m in RE_ENCARREGADO.finditer(p.texto_busca):
            contexto = p.texto_busca[max(0, m.start() - 300) : m.end() + 300]
            if RE_CONTEXTO_LGPD.search(contexto):
                mencoes.append((p, m))
    return mencoes


@avaliador("18.1")
def encarregado_lgpd(snap: Snapshot) -> Veredito:
    mencoes = _mencoes_encarregado(snap)
    if not mencoes:
        return _ausencia(snap, "Identificação do encarregado (LGPD)", snap.com_tema("lgpd") or None)
    for p, m in mencoes:
        # Margem larga: em páginas de LGPD o contato costuma vir num parágrafo próprio.
        janela = trecho(p.texto, m.start(), m.end(), margem=500)
        contato = RE_EMAIL.search(janela) or RE_TELEFONE.search(janela)
        if contato:
            return Veredito(
                S.ATENDE,
                0.7,
                "Menção ao encarregado com contato próximo (confirmar se identifica a pessoa).",
                [evidencia(p, "texto", "Encarregado e contato", trecho=janela)],
                detalhes={"zona_cinzenta": True},
            )
    p, m = mencoes[0]
    t = trecho(p.texto, m.start(), m.end())
    return Veredito(
        S.ATENDE_PARCIALMENTE,
        0.6,
        "Menção ao encarregado sem contato próximo.",
        [evidencia(p, "texto", "Menção ao encarregado", trecho=t)],
        detalhes={"zona_cinzenta": True},
    )
