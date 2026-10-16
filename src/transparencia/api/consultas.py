"""Consultas ao banco usadas pela API. Cada função recebe uma sessão e devolve esquemas.

Ficam separadas das rotas para poderem ser testadas e reutilizadas (o dashboard
consome a API, mas um script ou notebook pode chamar estas funções direto).
"""

from functools import lru_cache

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from transparencia.api import esquemas as e
from transparencia.db.models import (
    CatalogVersion,
    CrawlRun,
    Criterion,
    CriterionResult,
    Entity,
    EvaluationRun,
    Score,
    StatusExecucao,
)
from transparencia.evaluation.motor import ordem_mais_recente
from transparencia.evaluation.regras import carregar_regras
from transparencia.settings import get_settings

CONCLUIDAS = (StatusExecucao.SUCESSO, StatusExecucao.PARCIAL)


@lru_cache
def codigos_da_poc() -> frozenset[str]:
    return frozenset(carregar_regras(get_settings().evaluation_rules_path).codigos())


def _f(x) -> float | None:
    return None if x is None else float(x)


def _nota(s: Score | None) -> e.Nota | None:
    if s is None:
        return None
    return e.Nota(
        valor=_f(s.valor),
        valor_min=_f(s.valor_min),
        valor_max=_f(s.valor_max),
        cobertura=_f(s.cobertura),
        nivel=s.nivel,
        essenciais_ok=s.essenciais_ok,
    )


def _geral(run: EvaluationRun) -> Score | None:
    return next((s for s in run.scores if s.escopo == "geral"), None)


def _ordem(codigo: str) -> tuple[int, ...]:
    return tuple(int(x) for x in codigo.split("."))


# ------------------------------------------------------------ entidades


def _ultima_avaliacao(session: Session, entity_id: int) -> EvaluationRun | None:
    return session.scalar(
        select(EvaluationRun)
        .outerjoin(CrawlRun, CrawlRun.id == EvaluationRun.crawl_run_id)
        .where(EvaluationRun.entity_id == entity_id, EvaluationRun.status.in_(CONCLUIDAS))
        .order_by(*ordem_mais_recente())
        .options(selectinload(EvaluationRun.scores))
        .limit(1)
    )


def _resumo(session: Session, run: EvaluationRun | None) -> e.ResumoAvaliacao | None:
    if run is None:
        return None
    coleta = session.get(CrawlRun, run.crawl_run_id) if run.crawl_run_id else None
    pendentes = session.scalar(
        select(func.count())
        .select_from(CriterionResult)
        .where(CriterionResult.evaluation_run_id == run.id, CriterionResult.revisao_humana)
    )
    return e.ResumoAvaliacao(
        id=run.id,
        avaliado_em=run.started_at,
        coleta_id=run.crawl_run_id,
        coletado_em=coleta.started_at if coleta else None,
        versao_regras=run.versao_regras,
        nota=_nota(_geral(run)),
        revisoes_pendentes=pendentes or 0,
    )


def _entidade(session: Session, ent: Entity) -> e.Entidade:
    return e.Entidade(
        slug=ent.slug,
        nome=ent.nome,
        municipio=ent.municipio,
        uf=ent.uf,
        tipo=ent.tipo,
        urls=[
            e.Url(tipo=u.tipo, url=u.url, origem=u.origem, fornecedor=u.fornecedor)
            for u in ent.urls
            if u.ativo
        ],
        ultima_avaliacao=_resumo(session, _ultima_avaliacao(session, ent.id)),
    )


def listar_entidades(session: Session) -> list[e.Entidade]:
    ents = session.scalars(
        select(Entity)
        .where(Entity.ativo.is_(True))
        .order_by(Entity.nome)
        .options(selectinload(Entity.urls))
    )
    return [_entidade(session, ent) for ent in ents]


def buscar_entidade(session: Session, slug: str) -> Entity | None:
    return session.scalar(
        select(Entity).where(Entity.slug == slug).options(selectinload(Entity.urls))
    )


def detalhar_entidade(session: Session, slug: str) -> e.Entidade | None:
    ent = buscar_entidade(session, slug)
    return _entidade(session, ent) if ent else None


# ------------------------------------------------------------ avaliações


def detalhar_avaliacao(session: Session, run_id: int) -> e.Avaliacao | None:
    run = session.scalar(
        select(EvaluationRun)
        .where(EvaluationRun.id == run_id)
        .options(
            selectinload(EvaluationRun.scores),
            selectinload(EvaluationRun.results).selectinload(CriterionResult.criterion),
            selectinload(EvaluationRun.results).selectinload(CriterionResult.evidences),
        )
    )
    if run is None:
        return None
    ent = session.get(Entity, run.entity_id)
    coleta = session.get(CrawlRun, run.crawl_run_id) if run.crawl_run_id else None
    nomes_categoria = {
        r.criterion.categoria_codigo: r.criterion.categoria_nome for r in run.results
    }
    categorias = [
        e.NotaCategoria(
            categoria_codigo=s.categoria_codigo,
            categoria_nome=nomes_categoria.get(s.categoria_codigo, ""),
            **_nota(s).model_dump(),
        )
        for s in sorted(
            (s for s in run.scores if s.escopo == "categoria"),
            key=lambda s: int(s.categoria_codigo),
        )
    ]
    resultados = [
        e.Resultado(
            criterio=r.criterion.codigo,
            descricao=r.criterion.descricao,
            categoria_codigo=r.criterion.categoria_codigo,
            categoria_nome=r.criterion.categoria_nome,
            classificacao=r.criterion.classificacao,
            status=r.status,
            confianca=_f(r.confianca),
            metodo=r.metodo,
            justificativa=r.justificativa,
            revisao_humana=r.revisao_humana,
            llm_modelo=r.llm_modelo,
            evidencias=[
                e.Evidencia(
                    id=ev.id,
                    tipo=ev.tipo,
                    descricao=ev.descricao,
                    url=ev.url,
                    titulo_pagina=ev.titulo_pagina,
                    trecho=ev.trecho,
                    seletor=ev.seletor,
                    arquivo=f"/evidencias/{ev.id}/arquivo" if ev.arquivo_path else None,
                    metadados=ev.metadados,
                )
                for ev in sorted(r.evidences, key=lambda ev: ev.id)
            ],
        )
        for r in sorted(run.results, key=lambda r: _ordem(r.criterion.codigo))
    ]
    return e.Avaliacao(
        id=run.id,
        entidade=ent.slug,
        status=run.status,
        avaliado_em=run.started_at,
        coleta_id=run.crawl_run_id,
        coletado_em=coleta.started_at if coleta else None,
        versao_regras=run.versao_regras,
        custo_llm_usd=_f(run.custo_llm_usd),
        nota=_nota(_geral(run)),
        categorias=categorias,
        resultados=resultados,
    )


def ultima_avaliacao_id(session: Session, ent: Entity) -> int | None:
    run = _ultima_avaliacao(session, ent.id)
    return run.id if run else None


# ------------------------------------------------------------ histórico


def classificar_mudanca(de: str, para: str) -> str | None:
    """Mesma regra da view v_criterio_historico (migrations/sql/0002)."""
    if de == para:
        return None
    if para == "ATENDE" and de in ("NAO_ATENDE", "ATENDE_PARCIALMENTE"):
        return "CORRIGIDO"
    if para == "ATENDE_PARCIALMENTE" and de == "NAO_ATENDE":
        return "MELHOROU"
    if (para in ("NAO_ATENDE", "ATENDE_PARCIALMENTE") and de == "ATENDE") or (
        para == "NAO_ATENDE" and de == "ATENDE_PARCIALMENTE"
    ):
        return "REGREDIU"
    return "MUDOU"  # envolve NAO_AVALIADO / NAO_APLICAVEL


def historico(session: Session, ent: Entity, todas: bool = False) -> e.Historico:
    """Série de notas da entidade ao longo das coletas.

    Por padrão, UM ponto por coleta: a avaliação mais recente daquela coleta. Assim
    a série mostra a evolução do PORTAL, e não as reavaliações da mesma coleta com
    regras novas (que são auditoria: `todas=True` devolve todas). Como a avaliação
    roda sobre snapshots, coletas antigas podem ser reavaliadas com as regras
    atuais para a série inteira usar a mesma régua (`regua_unica`).
    """
    runs = list(
        session.scalars(
            select(EvaluationRun)
            .where(EvaluationRun.entity_id == ent.id, EvaluationRun.status.in_(CONCLUIDAS))
            .order_by(EvaluationRun.started_at)
            .options(
                selectinload(EvaluationRun.scores),
                selectinload(EvaluationRun.results).selectinload(CriterionResult.criterion),
            )
        )
    )
    if not todas:
        por_coleta: dict[int | None, EvaluationRun] = {}
        for run in runs:  # em ordem: a última de cada coleta prevalece
            por_coleta[run.crawl_run_id if run.crawl_run_id else -run.id] = run
        runs = list(por_coleta.values())
    coletas = {
        c.id: c
        for c in session.scalars(
            select(CrawlRun).where(
                CrawlRun.id.in_([r.crawl_run_id for r in runs if r.crawl_run_id])
            )
        )
    }

    def data_coleta(run: EvaluationRun):
        c = coletas.get(run.crawl_run_id)
        return c.started_at if c else run.started_at

    runs.sort(key=lambda r: (data_coleta(r), r.started_at))

    pontos, eventos, anterior = [], [], None
    for run in runs:
        nota = _nota(_geral(run))
        variacao = None
        if anterior is not None and nota and nota.valor is not None:
            nota_ant = _nota(_geral(anterior))
            if nota_ant and nota_ant.valor is not None:
                variacao = round(nota.valor - nota_ant.valor, 2)
        pontos.append(
            e.PontoHistorico(
                coleta_id=run.crawl_run_id,
                coletado_em=data_coleta(run),
                avaliacao_id=run.id,
                avaliado_em=run.started_at,
                versao_regras=run.versao_regras,
                nota=nota,
                variacao=variacao,
            )
        )
        if anterior is not None:
            antes = {r.criterion.codigo: r.status for r in anterior.results}
            for r in sorted(run.results, key=lambda r: _ordem(r.criterion.codigo)):
                de = antes.get(r.criterion.codigo)
                evento = classificar_mudanca(de, r.status) if de else None
                if evento:
                    eventos.append(
                        e.EventoCriterio(
                            criterio=r.criterion.codigo,
                            descricao=r.criterion.descricao,
                            de=de,
                            para=r.status,
                            evento=evento,
                            em=data_coleta(run),
                            avaliacao_id=run.id,
                        )
                    )
        anterior = run
    return e.Historico(
        entidade=ent.slug,
        regua_unica=len({p.versao_regras for p in pontos}) <= 1,
        pontos=pontos,
        eventos=eventos,
    )


# ------------------------------------------------------------ catálogo e revisão


def listar_criterios(session: Session, so_poc: bool = False) -> list[e.Criterio]:
    versao = session.scalar(select(CatalogVersion).order_by(CatalogVersion.id.desc()).limit(1))
    if versao is None:
        return []
    poc = codigos_da_poc()
    criterios = session.scalars(
        select(Criterion).where(Criterion.catalog_version_id == versao.id).order_by(Criterion.ordem)
    )
    return [
        e.Criterio(
            codigo=c.codigo,
            categoria_codigo=c.categoria_codigo,
            categoria_nome=c.categoria_nome,
            descricao=c.descricao,
            classificacao=c.classificacao,
            peso=float(c.peso),
            dimensao=c.dimensao,
            aplica_a=list(c.aplica_a),
            na_poc=c.codigo in poc,
        )
        for c in criterios
        if not so_poc or c.codigo in poc
    ]


def fila_de_revisao(session: Session) -> list[e.ItemRevisao]:
    """Resultados marcados para revisão humana na avaliação mais recente de cada entidade.

    É o produto principal hoje (docs/05_avaliacao.md §6): o sistema confirma o que
    está conforme e entrega aqui, com evidência, o que um humano precisa decidir.
    """
    itens = []
    for ent in session.scalars(select(Entity).where(Entity.ativo.is_(True)).order_by(Entity.nome)):
        run = _ultima_avaliacao(session, ent.id)
        if run is None:
            continue
        resultados = session.scalars(
            select(CriterionResult)
            .where(CriterionResult.evaluation_run_id == run.id, CriterionResult.revisao_humana)
            .options(
                selectinload(CriterionResult.criterion), selectinload(CriterionResult.evidences)
            )
        )
        for r in sorted(resultados, key=lambda r: _ordem(r.criterion.codigo)):
            ev = min(r.evidences, key=lambda ev: ev.id) if r.evidences else None
            itens.append(
                e.ItemRevisao(
                    entidade=ent.slug,
                    entidade_nome=ent.nome,
                    avaliacao_id=run.id,
                    criterio=r.criterion.codigo,
                    descricao=r.criterion.descricao,
                    status=r.status,
                    confianca=_f(r.confianca),
                    justificativa=r.justificativa,
                    evidencia_url=ev.url if ev else None,
                )
            )
    return itens
