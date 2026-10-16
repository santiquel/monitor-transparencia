"""Dashboard do Monitor de Transparência (Streamlit).

Rodar (com a API no ar):  uv run transparencia dashboard
O dashboard só fala com a API (API_URL no .env); nunca acessa o banco direto.

Escolhas de visualização:
- A nota nunca aparece sozinha: vem com a FAIXA (mín–máx, se os não avaliados
  fossem todos "não atende" ou todos "atende") e a COBERTURA. Uma nota 96 com
  47% de cobertura é muito menos firme que uma 90 com 85%.
- Status usam cores de status (verde/âmbar/vermelho/cinza) SEMPRE com ícone e
  rótulo: a cor nunca carrega o significado sozinha.
- Todo gráfico tem tooltip e uma tabela equivalente ao lado.
"""

from pathlib import Path

import altair as alt
import httpx
import pandas as pd
import streamlit as st

from transparencia.settings import PROJECT_ROOT, get_settings

API = get_settings().api_url.rstrip("/")

# Paleta de status (fixa): cor + ícone + rótulo andam sempre juntos.
STATUS = {
    "ATENDE": ("✅", "Atende", "#0ca30c"),
    "ATENDE_PARCIALMENTE": ("🟡", "Atende parcialmente", "#fab219"),
    "NAO_ATENDE": ("❌", "Não atende", "#d03b3b"),
    "NAO_AVALIADO": ("⚪", "Não avaliado (revisão)", "#9b9a94"),
    "NAO_APLICAVEL": ("➖", "Não se aplica", "#cfcec8"),
}
COR_NOTA = "#2a78d6"  # série única: a nota
COR_FAIXA = "#9b9a94"  # incerteza: neutra, recessiva


def rotulo(status: str) -> str:
    icone, texto, _ = STATUS.get(status, ("", status, ""))
    return f"{icone} {texto}"


@st.cache_data(ttl=60, show_spinner=False)
def api(caminho: str):
    r = httpx.get(f"{API}{caminho}", timeout=30)
    r.raise_for_status()
    return r.json()


def fmt(x, casas=1, sufixo="") -> str:
    return "—" if x is None else f"{x:.{casas}f}{sufixo}".replace(".", ",")


# ------------------------------------------------------------------ visão geral


def visao_geral() -> None:
    st.title("Monitor de Transparência")
    st.caption(
        "Conformidade dos portais de transparência com o checklist da LAI/LRF — "
        "19 critérios avaliados automaticamente, com evidências."
    )
    entidades = api("/entities")
    linhas = []
    for e in entidades:
        u = e["ultima_avaliacao"]
        n = (u or {}).get("nota") or {}
        linhas.append(
            {
                "slug": e["slug"],
                "Entidade": e["nome"]
                .replace("Prefeitura Municipal de ", "")
                .replace("Prefeitura da Cidade do ", ""),
                "Nota": n.get("valor"),
                "Nota revisada": ((u or {}).get("nota_revisada") or {}).get("valor"),
                "Mínimo": n.get("valor_min"),
                "Máximo": n.get("valor_max"),
                "Cobertura (%)": n.get("cobertura"),
                "Nível": n.get("nivel"),
                "A revisar": (u or {}).get("revisoes_pendentes"),
                "Coletado em": (u or {}).get("coletado_em", "")[:10] if u else None,
            }
        )
    df = pd.DataFrame(linhas)
    avaliadas = df.dropna(subset=["Nota"])

    c1, c2, c3 = st.columns(3)
    c1.metric("Entidades monitoradas", len(df))
    c2.metric("Avaliadas", len(avaliadas))
    c3.metric("Critérios aguardando revisão humana", int(df["A revisar"].fillna(0).sum()))

    st.subheader("Nota, faixa de incerteza e cobertura")
    st.caption(
        "O ponto é a nota sobre os critérios avaliados; a linha cinza vai do mínimo "
        "(não avaliados contados como 'não atende') ao máximo (contados como 'atende'). "
        "Linha longa = cobertura baixa = nota pouco firme."
    )
    if not avaliadas.empty:
        base = alt.Chart(avaliadas).encode(
            y=alt.Y("Entidade:N", sort=alt.SortField("Nota", order="descending"), title=None)
        )
        dica = [
            "Entidade",
            alt.Tooltip("Nota:Q", format=".1f"),
            alt.Tooltip("Mínimo:Q", format=".1f"),
            alt.Tooltip("Máximo:Q", format=".1f"),
            alt.Tooltip("Cobertura (%):Q", format=".0f"),
            "Nível",
        ]
        faixa = base.mark_rule(strokeWidth=4, strokeCap="round", color=COR_FAIXA).encode(
            x=alt.X("Mínimo:Q", scale=alt.Scale(domain=[0, 100]), title="Nota (0–100)"),
            x2="Máximo:Q",
            tooltip=dica,
        )
        ponto = base.mark_point(filled=True, size=140, color=COR_NOTA, opacity=1).encode(
            x="Nota:Q", tooltip=dica
        )
        rotulos = base.mark_text(align="left", dx=10, dy=-10, fontSize=11).encode(
            x="Nota:Q", text=alt.Text("Nota:Q", format=".0f")
        )
        st.altair_chart(
            (faixa + ponto + rotulos).properties(height=36 * len(avaliadas) + 40),
            width="stretch",
        )

    st.subheader("Status dos 19 critérios por entidade")
    contagens = []
    for e in entidades:
        if not e["ultima_avaliacao"]:
            continue
        aval = api(f"/entities/{e['slug']}/evaluations/latest")
        nome = next(r["Entidade"] for r in linhas if r["slug"] == e["slug"])
        for r in aval["resultados"]:
            contagens.append({"Entidade": nome, "status": r["status"]})
    if contagens:
        dc = pd.DataFrame(contagens).value_counts().reset_index(name="Critérios")
        dc["Status"] = dc["status"].map(rotulo)
        dc["ordem"] = dc["status"].map(list(STATUS).index)
        ordem_status = [rotulo(s) for s in STATUS]
        barras = (
            alt.Chart(dc)
            .mark_bar(height=18)
            .encode(
                y=alt.Y(
                    "Entidade:N",
                    title=None,
                    sort=alt.SortField("Entidade"),
                ),
                x=alt.X("Critérios:Q", title="Critérios", stack="zero"),
                color=alt.Color(
                    "Status:N",
                    scale=alt.Scale(domain=ordem_status, range=[c for *_, c in STATUS.values()]),
                    legend=alt.Legend(orient="top", title=None),
                ),
                order=alt.Order("ordem:Q"),
                tooltip=["Entidade", "Status", "Critérios"],
            )
            .properties(height=36 * dc["Entidade"].nunique() + 40)
        )
        st.altair_chart(barras, width="stretch")

    st.subheader("Tabela")
    st.dataframe(
        df.drop(columns=["slug"]),
        hide_index=True,
        width="stretch",
        column_config={
            "Nota": st.column_config.NumberColumn(format="%.1f"),
            "Nota revisada": st.column_config.NumberColumn(format="%.1f"),
            "Mínimo": st.column_config.NumberColumn(format="%.1f"),
            "Máximo": st.column_config.NumberColumn(format="%.1f"),
            "Cobertura (%)": st.column_config.ProgressColumn(
                min_value=0, max_value=100, format="%.0f%%"
            ),
        },
    )


# ------------------------------------------------------------------ entidade


def enviar(caminho: str, dados: dict | None = None) -> httpx.Response:
    """POST na API; limpa o cache para a tela mostrar o estado novo."""
    r = httpx.post(f"{API}{caminho}", json=dados, timeout=30)
    st.cache_data.clear()
    return r


def tem_veredito(r: dict) -> bool:
    """Há veredito humano valendo para este resultado (desta coleta ou herdado)?"""
    return r.get("veredito_humano") is not None


def avisar_depois(mensagem: str) -> None:
    """Guarda uma mensagem para mostrar DEPOIS do st.rerun() (senão ela some na hora)."""
    st.session_state["aviso"] = mensagem


def mostrar_aviso() -> None:
    if aviso := st.session_state.pop("aviso", None):
        st.success(aviso)


def painel_de_coleta(slug: str) -> None:
    """Botão para pedir nova coleta + avaliação e o estado dos pedidos."""
    with st.expander("🔄 Recoletar e reavaliar"):
        st.caption(
            "Põe um pedido na fila. Quem executa é o worker (`uv run transparencia worker` "
            "num terminal). A coleta usa as URLs indicadas nas revisões como pontos de partida."
        )
        if st.button("Pedir nova coleta", key=f"coletar-{slug}"):
            resp = enviar(f"/entities/{slug}/jobs")
            if resp.status_code in (200, 202):
                job = resp.json()
                novo = "na fila" if resp.status_code == 202 else "já estava na fila"
                st.success(f"Pedido {job['id']} {novo} ({job['status']}).")
            else:
                st.error(f"A API recusou o pedido: {resp.text}")
        jobs = api(f"/jobs?slug={slug}&limite=5")
        if jobs:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Pedido": j["id"],
                            "Status": j["status"],
                            "Tentativas": j["tentativas"],
                            "Criado em": j["criado_em"][:16].replace("T", " "),
                            "Resultado": (
                                f"coleta {j['resultado']['crawl_run_id']} → avaliação "
                                f"{j['resultado']['evaluation_run_id']}"
                                if j["resultado"]
                                else (j["ultimo_erro"] or "")
                            ),
                        }
                        for j in jobs
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
            if st.button("Atualizar", key=f"atualizar-{slug}"):
                st.cache_data.clear()
                st.rerun()


OPCOES_VEREDITO = [None, "ATENDE", "ATENDE_PARCIALMENTE", "NAO_ATENDE", "NAO_APLICAVEL"]


def formulario_revisao(r: dict, coleta_id: int | None, entidade: str) -> None:
    """Registra o veredito humano e/ou a URL correta do critério.

    O formulário é identificado por ENTIDADE + CRITÉRIO, não pelo id do resultado:
    se o worker terminar uma coleta enquanto a pessoa preenche, a avaliação nova
    tem ids novos, e um formulário identificado pelo id antigo sumiria da tela no
    envio (o que já aconteceu: a revisão se perdia em silêncio).
    """
    vale = r.get("veredito_humano")
    if vale:
        origem = (
            "nesta coleta"
            if vale["coleta_id"] == coleta_id
            else f"herdado da coleta {vale['coleta_id']} (a máquina mantém a mesma conclusão)"
        )
        st.success(f"👤 Veredito humano: {rotulo(vale['status'])}, {origem}.")
    rev = r.get("revisao")
    if rev:
        quando = rev["criado_em"][:16].replace("T", " ")
        partes = [f"Última revisão: {quando}, {rev['revisor'] or 'sem nome'}"]
        if rev["url_indicada"]:
            partes.append(f"URL indicada: {rev['url_indicada']}")
        st.info(" · ".join(partes) + (f"\n\n{rev['comentario']}" if rev["comentario"] else ""))

    chave = f"{entidade}-{r['criterio']}"
    with st.form(key=f"revisao-{chave}"):
        st.markdown("**Revisar este critério**")
        status = st.selectbox(
            "Veredito",
            OPCOES_VEREDITO,
            format_func=lambda s: "— sem veredito (só indicar a URL) —" if s is None else rotulo(s),
            key=f"status-{chave}",
        )
        url = st.text_input(
            "URL correta para este critério (opcional)",
            placeholder="https://… a página onde a informação realmente está",
            key=f"url-{chave}",
        )
        comentario = st.text_area("Comentário (opcional)", key=f"coment-{chave}")
        revisor = st.text_input(
            "Nome do revisor", value=st.session_state.get("revisor", ""), key=f"revisor-{chave}"
        )
        if st.form_submit_button("Registrar revisão"):
            st.session_state["revisor"] = revisor
            dados = {
                "status": status,
                "url_indicada": url.strip() or None,
                "comentario": comentario.strip() or None,
                "revisor": revisor.strip() or None,
            }
            resp = enviar(f"/results/{r['id']}/review", dados)
            if resp.status_code == 201:
                avisar_depois(
                    f"Revisão do critério {r['criterio']} registrada."
                    + (
                        " A URL entra na próxima coleta (use 'Recoletar e reavaliar')."
                        if dados["url_indicada"]
                        else ""
                    )
                )
                st.rerun()
            else:
                detalhe = resp.json().get("detail", resp.text)
                st.error(f"Não foi possível registrar: {detalhe}")


def pagina_entidade() -> None:
    entidades = api("/entities")
    nomes = {e["nome"]: e["slug"] for e in entidades}
    escolha = st.selectbox("Entidade", list(nomes))
    slug = nomes[escolha]
    ent = next(e for e in entidades if e["slug"] == slug)
    st.title(escolha)
    for u in ent["urls"]:
        forn = f" · fornecedor: {u['fornecedor']}" if u["fornecedor"] else ""
        st.caption(f"{u['tipo'].capitalize()}: {u['url']} ({u['origem']}{forn})")
    if not ent["ultima_avaliacao"]:
        st.info("Esta entidade ainda não foi avaliada.")
        return

    aval = api(f"/entities/{slug}/evaluations/latest")
    n = aval["nota"] or {}
    nr = aval["nota_revisada"]
    c = st.columns(6)
    c[0].metric("Nota (máquina)", fmt(n.get("valor")))
    c[1].metric(
        "Nota revisada",
        fmt(nr["valor"]) if nr else "—",
        help=f"Mesma fórmula, com o veredito humano em {aval['revisados']} critério(s)."
        if nr
        else "Nenhum critério desta coleta recebeu veredito humano.",
    )
    c[2].metric("Faixa", f"{fmt(n.get('valor_min'), 0)} – {fmt(n.get('valor_max'), 0)}")
    c[3].metric("Cobertura", fmt(n.get("cobertura"), 0, "%"))
    c[4].metric("Nível", n.get("nivel") or "—")
    pendentes = sum(r["revisao_humana"] and not tem_veredito(r) for r in aval["resultados"])
    c[5].metric("A revisar", pendentes)
    st.caption(
        f"Avaliação {aval['id']} sobre a coleta {aval['coleta_id']} "
        f"({(aval['coletado_em'] or '')[:10]}), regras {aval['versao_regras']}."
    )
    painel_de_coleta(slug)

    aba_crit, aba_hist, aba_cat = st.tabs(["Critérios e evidências", "Histórico", "Categorias"])
    with aba_crit:
        filtro = st.multiselect(
            "Status", list(STATUS), format_func=rotulo, placeholder="Todos os status"
        )
        for r in aval["resultados"]:
            if filtro and r["status_final"] not in filtro:
                continue
            if tem_veredito(r):
                herdado = r["veredito_humano"]["coleta_id"] != aval["coleta_id"]
                marca = (
                    f" · 👤 revisado{' (herdado)' if herdado else ''}: {rotulo(r['status_final'])}"
                )
            elif r["revisao_humana"]:
                marca = " · 👁 revisar"
            else:
                marca = ""
            titulo = f"{rotulo(r['status'])} — **{r['criterio']}** {r['descricao']}{marca}"
            with st.expander(titulo):
                st.write(r["justificativa"] or "")
                st.caption(
                    f"{r['classificacao'].capitalize()} · método {r['metodo']} · "
                    f"confiança {fmt(r['confianca'], 2)}"
                    + (f" · modelo {r['llm_modelo']}" if r["llm_modelo"] else "")
                )
                for ev in r["evidencias"]:
                    st.markdown(f"**{ev['descricao']}** ({ev['tipo']})")
                    if ev["url"]:
                        st.markdown(f"Página: [{ev['titulo_pagina'] or ev['url']}]({ev['url']})")
                    if ev["trecho"]:
                        st.code(ev["trecho"], language=None, wrap_lines=True)
                    if ev["seletor"]:
                        st.caption(f"Elemento: `{ev['seletor']}`")
                    if ev["arquivo"]:
                        st.markdown(f"[Abrir o snapshot guardado]({API}{ev['arquivo']})")
                st.divider()
                formulario_revisao(r, aval["coleta_id"], slug)

    with aba_hist:
        hist = api(f"/entities/{slug}/history")
        pontos = pd.DataFrame(
            [
                {
                    "Coleta": p["coletado_em"],
                    "Nota": (p["nota"] or {}).get("valor"),
                    "Mínimo": (p["nota"] or {}).get("valor_min"),
                    "Máximo": (p["nota"] or {}).get("valor_max"),
                    "Cobertura (%)": (p["nota"] or {}).get("cobertura"),
                    "Variação": p["variacao"],
                    "Regras": p["versao_regras"],
                }
                for p in hist["pontos"]
            ]
        )
        if not hist["regua_unica"]:
            st.warning(
                "Os pontos usam versões diferentes das regras: a variação mistura mudança do "
                "portal com mudança de metodologia. Rode `transparencia reprocessar`."
            )
        if not pontos.empty:
            pontos["Coleta"] = pd.to_datetime(pontos["Coleta"])
            dica = [
                alt.Tooltip("Coleta:T", format="%d/%m/%Y %H:%M"),
                alt.Tooltip("Nota:Q", format=".1f"),
                alt.Tooltip("Mínimo:Q", format=".1f"),
                alt.Tooltip("Máximo:Q", format=".1f"),
                alt.Tooltip("Cobertura (%):Q", format=".0f"),
            ]
            base = alt.Chart(pontos).encode(
                x=alt.X("Coleta:T", title=None, axis=alt.Axis(format="%d/%m", tickCount="day"))
            )
            faixa = base.mark_area(opacity=0.18, color=COR_FAIXA).encode(
                y=alt.Y("Mínimo:Q", scale=alt.Scale(domain=[0, 100]), title="Nota"),
                y2="Máximo:Q",
            )
            linha = base.mark_line(strokeWidth=2, color=COR_NOTA).encode(y="Nota:Q")
            marcas = base.mark_point(filled=True, size=90, color=COR_NOTA).encode(
                y="Nota:Q", tooltip=dica
            )
            st.altair_chart((faixa + linha + marcas).properties(height=280), width="stretch")
            st.caption("Faixa cinza: mínimo–máximo de cada coleta. Um ponto por coleta.")
            st.dataframe(pontos, hide_index=True, width="stretch")
        st.subheader("O que mudou entre coletas")
        if hist["eventos"]:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Em": (ev["em"] or "")[:10],
                            "Critério": ev["criterio"],
                            "Descrição": ev["descricao"],
                            "De": rotulo(ev["de"]),
                            "Para": rotulo(ev["para"]),
                            "Evento": ev["evento"],
                        }
                        for ev in hist["eventos"]
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("Nenhum critério mudou de status.")

    with aba_cat:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Categoria": f"{c['categoria_codigo']}. {c['categoria_nome']}",
                        "Nota": c["valor"],
                        "Mínimo": c["valor_min"],
                        "Máximo": c["valor_max"],
                        "Cobertura (%)": c["cobertura"],
                    }
                    for c in aval["categorias"]
                ]
            ),
            hide_index=True,
            width="stretch",
        )


# ------------------------------------------------------------------ revisão


def fila_revisao() -> None:
    st.title("Fila de revisão humana")
    st.caption(
        "O que o sistema não pôde concluir com segurança (dados atrás de consultas, "
        "ausência não comprovada, zona cinzenta) ou concluiu com confiança baixa. "
        "Cada item traz o motivo e a página de evidência para conferir."
    )
    fila = pd.DataFrame(api("/review"))
    if fila.empty:
        st.success("Nada aguardando revisão.")
        return
    escolha = st.multiselect(
        "Entidades", sorted(fila["entidade_nome"].unique()), placeholder="Todas"
    )
    if escolha:
        fila = fila[fila["entidade_nome"].isin(escolha)]
    st.metric("Itens", len(fila))
    fila["Status"] = fila["status"].map(rotulo)
    st.dataframe(
        fila[
            ["entidade_nome", "criterio", "descricao", "Status", "justificativa", "evidencia_url"]
        ],
        hide_index=True,
        width="stretch",
        column_config={
            "entidade_nome": "Entidade",
            "criterio": "Critério",
            "descricao": "Descrição",
            "justificativa": st.column_config.TextColumn("Motivo", width="large"),
            "evidencia_url": st.column_config.LinkColumn("Evidência"),
        },
    )

    st.subheader("Revisar um item")
    itens = fila.to_dict("records")
    item = st.selectbox(
        "Item",
        itens,
        format_func=lambda i: f"{i['entidade_nome']} — {i['criterio']} {i['descricao']}",
    )
    aval = api(f"/evaluations/{item['avaliacao_id']}")
    resultado = next(r for r in aval["resultados"] if r["id"] == item["resultado_id"])
    st.write(resultado["justificativa"] or "")
    for ev in resultado["evidencias"][:3]:
        if ev["url"]:
            st.markdown(f"Evidência: [{ev['titulo_pagina'] or ev['url']}]({ev['url']})")
    formulario_revisao(resultado, aval["coleta_id"], item["entidade"])
    st.caption(
        "Para recoletar com a URL indicada, abra a entidade na página 'Entidade' e use "
        "'Recoletar e reavaliar'."
    )


# ------------------------------------------------------------------ metodologia


def metodologia() -> None:
    st.title("Metodologia e qualidade")
    st.markdown(
        """
**Como a nota é calculada.** Média ponderada dos critérios avaliados (essencial 2,
obrigatório 1,5, recomendado 1 — pesos do PNTP): atende = 1, parcial = 0,5,
não atende = 0. "Não se aplica" sai da conta. "Não avaliado" também sai, mas
aparece na **faixa** (mínimo e máximo possíveis) e na **cobertura**.

**Como cada critério é decidido.** Regras determinísticas sobre snapshots da
coleta (HTML, PDFs, screenshots), sempre com evidência: URL, trecho literal e
elemento da página. O sistema só afirma um descumprimento com prova completa;
na dúvida, encaminha para revisão humana.

**Escopo.** 19 dos 142 critérios do checklist têm regra nesta prova de conceito.

Detalhes: `docs/05_avaliacao.md`.
"""
    )
    medicoes = sorted((PROJECT_ROOT / "golden" / "medicoes").glob("*.md"))
    if medicoes:
        ultima: Path = medicoes[-1]
        st.subheader("Última medição contra o golden set (rótulos manuais)")
        st.caption(f"Arquivo: golden/medicoes/{ultima.name}")
        texto = ultima.read_text(encoding="utf-8")
        inicio, fim = texto.find("## Resumo"), texto.find("## Precisão")
        st.markdown(texto[inicio:fim] if inicio >= 0 else texto)


# ------------------------------------------------------------------ navegação


def main() -> None:
    st.set_page_config(page_title="Monitor de Transparência", page_icon="🔎", layout="wide")
    try:
        api("/health")
    except httpx.HTTPError:
        st.error(
            f"A API não respondeu em {API}. Em outro terminal, rode "
            "`uv run transparencia api` e recarregue esta página."
        )
        st.stop()
    paginas = {
        "Visão geral": visao_geral,
        "Entidade": pagina_entidade,
        "Fila de revisão": fila_revisao,
        "Metodologia": metodologia,
    }
    mostrar_aviso()
    escolha = st.sidebar.radio("Navegação", list(paginas))
    st.sidebar.caption(f"API: {API}")
    paginas[escolha]()


main()
