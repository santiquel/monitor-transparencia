"""Linha de comando do projeto.

Exemplos (no terminal do VSCode, na raiz do projeto):
    uv run transparencia --help
    uv run transparencia catalogo gerar
    uv run transparencia catalogo resumo
    uv run transparencia seed
    uv run transparencia coletar japeri
    uv run transparencia coletar --todas --max-paginas 30
    uv run transparencia avaliar --todas
    uv run transparencia golden medir --nome so-regras
"""

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console
from rich.table import Table

from transparencia.catalog.builder import construir_catalogo
from transparencia.catalog.loader import carregar_catalogo, carregar_entidades
from transparencia.settings import PROJECT_ROOT, get_settings

app = typer.Typer(help="Monitor de conformidade de portais de transparência.", no_args_is_help=True)
catalogo_app = typer.Typer(help="Catálogo de critérios (derivado do checklist).")
app.add_typer(catalogo_app, name="catalogo")
console = Console()

XLSX_PADRAO = PROJECT_ROOT / "docs" / "referencia" / "checklistPortalTransp.xlsx"

CABECALHO_YAML = """\
# =============================================================================
# CATÁLOGO DE CRITÉRIOS — ARQUIVO GERADO AUTOMATICAMENTE
# Fonte: docs/referencia/checklistPortalTransp.xlsx
# Gerador: src/transparencia/catalog/builder.py
# Comando: uv run transparencia catalogo gerar
#
# Não edite à mão: altere o gerador (decisões ficam versionadas e explicadas lá)
# e rode o comando de novo. Revise a diferença com `git diff`.
# =============================================================================
"""


@catalogo_app.command("gerar")
def catalogo_gerar(
    xlsx: Annotated[Path, typer.Option(help="Planilha de quesitos")] = XLSX_PADRAO,
    saida: Annotated[Path | None, typer.Option(help="YAML de saída")] = None,
) -> None:
    """Converte o checklist .xlsx no catálogo YAML versionado."""
    saida = saida or get_settings().criteria_catalog_path
    catalogo = construir_catalogo(xlsx)
    dados = catalogo.model_dump(mode="json", exclude_none=True)
    texto = yaml.safe_dump(dados, allow_unicode=True, sort_keys=False, width=100)
    saida.write_text(CABECALHO_YAML + texto, encoding="utf-8")
    console.print(
        f"[green]OK[/green] {len(catalogo.criterios)} critérios em "
        f"{len(catalogo.categorias)} categorias -> {saida}"
    )
    for obs in catalogo.observacoes_fonte:
        console.print(f"[yellow]Observação sobre a fonte:[/yellow] {obs}")


@catalogo_app.command("resumo")
def catalogo_resumo() -> None:
    """Mostra uma tabela-resumo do catálogo (por categoria e classificação)."""
    cat = carregar_catalogo(get_settings().criteria_catalog_path)
    tabela = Table(title=f"Catálogo {cat.versao} — hash {cat.content_hash()[:12]}")
    for col in ("Cat.", "Nome", "Matriz", "Crit.", "Ess.", "Obr.", "Rec.", "Aplica a"):
        tabela.add_column(col)
    for c in cat.categorias:
        crits = [x for x in cat.criterios if x.categoria == c.codigo]
        cont = Counter(x.classificacao.value for x in crits)
        aplica = sorted({p for x in crits for p in x.aplica_a})
        tabela.add_row(
            c.codigo,
            c.nome,
            c.matriz.value,
            str(len(crits)),
            str(cont["essencial"]),
            str(cont["obrigatorio"]),
            str(cont["recomendado"]),
            ", ".join(aplica),
        )
    console.print(tabela)
    console.print(f"Total de critérios: {len(cat.criterios)}")


@app.command("seed")
def seed() -> None:
    """Carrega catálogo e entidades (YAML) no PostgreSQL. Pode rodar várias vezes."""
    from transparencia.db.seed import executar_seed
    from transparencia.db.session import session_scope

    s = get_settings()
    catalogo = carregar_catalogo(s.criteria_catalog_path)
    cadastro = carregar_entidades(s.entities_path)
    with session_scope() as session:
        r = executar_seed(session, catalogo, cadastro)
    estado = "nova versão gravada" if r.catalogo_novo else "já existia (sem mudanças)"
    console.print(f"[green]Catálogo[/green] id={r.catalogo_versao_id}: {estado}")
    console.print(
        f"[green]Entidades[/green]: {r.entidades_criadas} criadas, "
        f"{r.entidades_atualizadas} atualizadas"
    )


def _mostrar_pagina(p) -> None:
    cor = "green" if p.ok else "red"
    status = p.http_status or "---"
    temas = f" [dim]{','.join(p.temas)}[/dim]" if p.temas else ""
    console.print(
        f"  [{cor}]{status}[/{cor}] {p.modo_render or '-':7} p{p.profundidade} "
        f"{p.url_final or p.url}{temas}" + (f" [red]{p.erro[:80]}[/red]" if p.erro else ""),
        highlight=False,
    )


@app.command("coletar")
def coletar(
    slugs: Annotated[list[str] | None, typer.Argument(help="Slugs das entidades")] = None,
    todas: Annotated[
        bool, typer.Option("--todas", help="Coleta todas as entidades ativas")
    ] = False,
    max_paginas: Annotated[int, typer.Option(help="Orçamento de páginas HTML")] = 60,
    max_documentos: Annotated[int, typer.Option(help="Orçamento de PDFs/planilhas")] = 25,
    profundidade: Annotated[int, typer.Option(help="Cliques a partir das sementes")] = 3,
    sem_navegador: Annotated[bool, typer.Option(help="Só HTTP simples (sem Playwright)")] = False,
) -> None:
    """Visita site e portal da entidade e guarda os snapshots (data/ + tabela pages)."""
    from sqlalchemy import select

    from transparencia.collector.coletor import ParametrosColeta
    from transparencia.collector.execucao import executar_coleta
    from transparencia.db.models import Entity
    from transparencia.db.session import session_scope

    cadastro = {e.slug: e for e in carregar_entidades(get_settings().entities_path).entidades}
    if todas:
        slugs = [slug for slug, e in cadastro.items() if e.ativo]
    if not slugs:
        raise typer.BadParameter("informe ao menos um slug ou use --todas")
    desconhecidos = [s for s in slugs if s not in cadastro]
    if desconhecidos:
        raise typer.BadParameter(f"slugs fora de config/entities.yaml: {desconhecidos}")

    resumo = Table(title="Resumo da coleta")
    for col in ("Entidade", "Run", "Status", "Páginas OK", "Com erro", "Fornecedor", "Avisos"):
        resumo.add_column(col)

    for slug in slugs:
        params = ParametrosColeta(
            max_paginas=max_paginas,
            max_documentos=max_documentos,
            profundidade_max=profundidade,
            usar_navegador=not sem_navegador,
            ignorar_robots=cadastro[slug].ignorar_robots,
        )
        with session_scope() as session:
            entity = session.scalar(select(Entity).where(Entity.slug == slug))
            if entity is None:
                console.print(f"[red]{slug}[/red] não está no banco: rode `transparencia seed`.")
                continue
            console.print(f"\n[bold]{entity.nome}[/bold]")
            run = executar_coleta(session, entity, params, ao_coletar=_mostrar_pagina)
            resumo.add_row(
                slug,
                str(run.id),
                run.status,
                str(run.paginas_coletadas),
                str(run.paginas_com_erro),
                run.fornecedor_detectado or "-",
                (run.erro or "")[:60],
            )
    console.print(resumo)


CORES_STATUS = {
    "ATENDE": "green",
    "ATENDE_PARCIALMENTE": "yellow",
    "NAO_ATENDE": "red",
    "NAO_AVALIADO": "bright_black",
    "NAO_APLICAVEL": "blue",
}


def _criar_juiz(s, versao_regras: str):
    """Monta o juiz de IA conforme LLM_PROVEDOR; None (só regras) se não houver como usar."""
    from transparencia.evaluation.ia import JuizIA, ProvedorAnthropic, ProvedorOllama

    if s.llm_provedor == "nenhum":
        console.print("[yellow]LLM_PROVEDOR=nenhum: avaliando só com regras.[/yellow]")
        return None
    if s.llm_provedor == "anthropic":
        if not s.anthropic_api_key:
            console.print("[yellow]ANTHROPIC_API_KEY ausente: avaliando só com regras.[/yellow]")
            return None
        import anthropic

        provedor = ProvedorAnthropic(anthropic.Anthropic(api_key=s.anthropic_api_key))
    else:
        provedor = ProvedorOllama(s.ollama_url)
        instalados = provedor.modelos_instalados()
        if instalados is None:
            console.print(
                f"[yellow]Ollama não respondeu em {s.ollama_url} (está aberto?): "
                "avaliando só com regras.[/yellow]"
            )
            return None
        if s.llm_model not in instalados:
            console.print(
                f"[yellow]Modelo {s.llm_model} não baixado (rode `ollama pull {s.llm_model}`): "
                "avaliando só com regras.[/yellow]"
            )
            return None
    console.print(f"IA: {provedor.nome} / {s.llm_model}")
    return JuizIA(provedor, s.llm_model, s.data_dir / "cache_llm", versao_regras)


@app.command("avaliar")
def avaliar(
    slugs: Annotated[list[str] | None, typer.Argument(help="Slugs das entidades")] = None,
    todas: Annotated[
        bool, typer.Option("--todas", help="Avalia todas as entidades ativas")
    ] = False,
    coleta: Annotated[
        int | None, typer.Option(help="Id da coleta (padrão: a mais recente concluída)")
    ] = None,
    sem_ia: Annotated[
        bool, typer.Option(help="Só regras determinísticas (ignora LLM_PROVEDOR)")
    ] = False,
) -> None:
    """Avalia os critérios da PoC sobre a coleta mais recente (sem acessar o site)."""
    from sqlalchemy import select

    from transparencia.db.models import CatalogVersion, CrawlRun, Entity
    from transparencia.db.session import session_scope
    from transparencia.evaluation.motor import executar_avaliacao
    from transparencia.evaluation.regras import carregar_regras

    s = get_settings()
    regras = carregar_regras(s.evaluation_rules_path)
    juiz = None if sem_ia else _criar_juiz(s, regras.versao)
    hash_catalogo = carregar_catalogo(s.criteria_catalog_path).content_hash()
    with session_scope() as session:
        versao = session.scalar(
            select(CatalogVersion).where(CatalogVersion.content_hash == hash_catalogo)
        )
        if versao is None:
            console.print("[red]Catálogo atual não está no banco: rode `transparencia seed`.[/red]")
            raise typer.Exit(1)
        if todas:
            slugs = list(session.scalars(select(Entity.slug).where(Entity.ativo.is_(True))))
        if not slugs:
            raise typer.BadParameter("informe ao menos um slug ou use --todas")

        for slug in slugs:
            entity = session.scalar(select(Entity).where(Entity.slug == slug))
            if entity is None:
                console.print(f"[red]{slug}[/red] não está no banco.")
                continue
            crawl = session.get(CrawlRun, coleta) if coleta else None
            if crawl is not None and crawl.entity_id != entity.id:
                console.print(f"[red]A coleta {coleta} não é de {slug}: pulando.[/red]")
                continue
            run, resultados = executar_avaliacao(session, entity, regras, versao, crawl, juiz=juiz)
            session.commit()

            tabela = Table(title=f"{entity.nome} — avaliação {run.id} (coleta {run.crawl_run_id})")
            for col in ("Critério", "Status", "Conf.", "Revisar", "Justificativa"):
                tabela.add_column(col)
            for r in resultados:
                v = r.veredito
                cor = CORES_STATUS[v.status]
                tabela.add_row(
                    r.criterio.codigo,
                    f"[{cor}]{v.status}[/{cor}]",
                    f"{v.confianca:.2f}",
                    "sim" if v.revisao_humana else "",
                    v.justificativa,
                )
            console.print(tabela)
            if run.custo_llm_usd:
                console.print(f"Custo de IA nesta avaliação: US$ {run.custo_llm_usd:.5f}")


@app.command("reprocessar")
def reprocessar(
    slugs: Annotated[list[str] | None, typer.Argument(help="Slugs (padrão: todas)")] = None,
) -> None:
    """Reavalia, com as regras ATUAIS, toda coleta que já foi avaliada antes.

    Não acessa os sites: usa os snapshots. Serve para o histórico comparar coletas
    com a mesma régua depois de uma mudança nas regras. É idempotente (pula a
    coleta que já tem avaliação desta versão) e não apaga nada: as avaliações
    antigas continuam no banco (histórico append-only), com origem 'manual'.
    """
    from sqlalchemy import select

    from transparencia.db.models import CatalogVersion, CrawlRun, Entity, EvaluationRun
    from transparencia.db.session import session_scope
    from transparencia.evaluation.motor import executar_avaliacao, versao_regua
    from transparencia.evaluation.regras import carregar_regras

    s = get_settings()
    regras = carregar_regras(s.evaluation_rules_path)
    versao_atual = versao_regua(regras)
    hash_catalogo = carregar_catalogo(s.criteria_catalog_path).content_hash()
    feitas = puladas = 0
    with session_scope() as session:
        catalogo = session.scalar(
            select(CatalogVersion).where(CatalogVersion.content_hash == hash_catalogo)
        )
        consulta = select(Entity).where(Entity.ativo.is_(True))
        if slugs:
            consulta = select(Entity).where(Entity.slug.in_(slugs))
        for entity in session.scalars(consulta.order_by(Entity.slug)).all():
            avaliacoes = session.scalars(
                select(EvaluationRun).where(
                    EvaluationRun.entity_id == entity.id, EvaluationRun.crawl_run_id.is_not(None)
                )
            ).all()
            coletas = sorted({a.crawl_run_id for a in avaliacoes})
            atuais = {a.crawl_run_id for a in avaliacoes if a.versao_regras == versao_atual}
            for coleta_id in coletas:
                if coleta_id in atuais:
                    puladas += 1
                    continue
                run, _ = executar_avaliacao(
                    session,
                    entity,
                    regras,
                    catalogo,
                    session.get(CrawlRun, coleta_id),
                    origem="reprocessamento",
                )
                session.commit()
                feitas += 1
                console.print(f"{entity.slug}: coleta {coleta_id} -> avaliação {run.id}")
    console.print(
        f"[green]OK[/green] {feitas} reavaliadas com {versao_atual}; {puladas} já estavam nela."
    )


@app.command("api")
def api(
    porta: Annotated[int, typer.Option(help="Porta HTTP")] = 8000,
    host: Annotated[str, typer.Option(help="0.0.0.0 para aceitar conexões de fora")] = "127.0.0.1",
    recarregar: Annotated[bool, typer.Option(help="Reinicia ao salvar código (dev)")] = False,
) -> None:
    """Sobe a API HTTP (documentação interativa em http://localhost:PORTA/docs)."""
    import uvicorn

    uvicorn.run("transparencia.api.app:app", host=host, port=porta, reload=recarregar)


golden_app = typer.Typer(help="Golden set: rótulos manuais para medir os avaliadores.")
app.add_typer(golden_app, name="golden")


@golden_app.command("modelo")
def golden_modelo(
    entidades: Annotated[
        list[str] | None, typer.Option("--entidade", help="Slug (repita para várias)")
    ] = None,
    sobrescrever: Annotated[
        bool, typer.Option(help="Substitui uma planilha existente (apaga os rótulos!)")
    ] = False,
) -> None:
    """Gera a planilha de rotulagem (golden/golden_set.xlsx) a partir da rubrica."""
    from transparencia.evaluation.golden import ENTIDADES_PADRAO, gerar_modelo
    from transparencia.evaluation.regras import carregar_regras

    s = get_settings()
    if s.golden_path.exists() and not sobrescrever:
        console.print(
            f"[red]{s.golden_path} já existe.[/red] Para não apagar rótulos, nada foi feito. "
            "Use --sobrescrever se quiser mesmo recomeçar."
        )
        raise typer.Exit(1)
    linhas = gerar_modelo(
        s.golden_path,
        carregar_regras(s.evaluation_rules_path),
        carregar_catalogo(s.criteria_catalog_path),
        carregar_entidades(s.entities_path),
        entidades or ENTIDADES_PADRAO,
    )
    console.print(f"[green]OK[/green] {linhas} linhas para rotular -> {s.golden_path}")


@golden_app.command("medir")
def golden_medir(
    nome: Annotated[
        str, typer.Option(help="Rótulo da medição, vai no nome do arquivo (ex.: so-regras)")
    ] = "medicao",
    avaliacao: Annotated[
        list[int] | None,
        typer.Option(help="Id da avaliação a medir (repita; padrão: a mais recente de cada)"),
    ] = None,
) -> None:
    """Compara as avaliações com o golden set e grava o relatório em golden/medicoes/."""
    from datetime import datetime

    from sqlalchemy import select

    from transparencia.db.models import CrawlRun
    from transparencia.db.session import session_scope
    from transparencia.evaluation.medicao import (
        escolher_avaliacoes,
        gerar_relatorio,
        gravar_csv,
        ler_rotulos,
        medir,
        pct,
        previsoes_da_avaliacao,
    )
    from transparencia.evaluation.motor import versao_regua
    from transparencia.evaluation.regras import carregar_regras

    s = get_settings()
    rotulos = ler_rotulos(s.golden_path)
    slugs = sorted({r.entidade for r in rotulos})
    with session_scope() as session:
        avaliacoes = escolher_avaliacoes(session, slugs, avaliacao)
        faltando = [slug for slug in slugs if slug not in avaliacoes]
        if faltando:
            console.print(
                f"[yellow]Sem avaliação para {faltando}: casos contam como NAO_AVALIADO.[/yellow]"
            )
        previsoes = [
            p for slug, run in avaliacoes.items() for p in previsoes_da_avaliacao(slug, run)
        ]
        ids_coleta = [r.crawl_run_id for r in avaliacoes.values() if r.crawl_run_id]
        coletas = {
            c.id: c for c in session.scalars(select(CrawlRun).where(CrawlRun.id.in_(ids_coleta)))
        }
        m = medir(rotulos, previsoes)
        relatorio = gerar_relatorio(
            m, nome, avaliacoes, coletas, versao_regua(carregar_regras(s.evaluation_rules_path))
        )

    pasta = s.golden_path.parent / "medicoes"
    pasta.mkdir(parents=True, exist_ok=True)
    base = pasta / f"{datetime.now():%Y-%m-%d_%H%M}_{nome}"
    base.with_suffix(".md").write_text(relatorio, encoding="utf-8")
    gravar_csv(m, base.with_suffix(".csv"))

    t = Table(title=f"Golden set — {nome}")
    t.add_column("Métrica")
    t.add_column("Valor", justify="right")
    t.add_row("Casos rotulados", f"{m.n} (+{m.ignorados_nao_sei} NAO_SEI)")
    t.add_row("Cobertura", f"{pct(m.cobertura)} ({m.decididos}/{m.n})")
    t.add_row("Acurácia nos decididos", f"{pct(m.acuracia)} ({m.acertos}/{m.decididos})")
    t.add_row(f"Baseline (sempre {m.baseline_classe})", pct(m.baseline_acuracia))
    t.add_row("Kappa de Cohen", "—" if m.kappa is None else f"{m.kappa:.2f}")
    nao_atende = m.por_status["NAO_ATENDE"]
    t.add_row(
        "NAO_ATENDE precisão / recall",
        f"{pct(nao_atende['precisao'])} / {pct(nao_atende['recall'])}",
    )
    t.add_row("Acusações falsas", str(len(m.acusacoes_falsas)))
    t.add_row("Erros sinalizados p/ revisão", f"{m.erros_sinalizados}/{len(m.divergencias)}")
    console.print(t)
    console.print(f"Relatório: {base.with_suffix('.md')}\nCasos (CSV): {base.with_suffix('.csv')}")


if __name__ == "__main__":
    app()
