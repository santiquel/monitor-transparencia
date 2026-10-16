"""Consultas ao banco usadas pela API. Cada função recebe uma sessão e devolve esquemas.

Ficam separadas das rotas para poderem ser testadas e reutilizadas (o dashboard
consome a API, mas um script ou notebook pode chamar estas funções direto).
"""

from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from transparencia.api import esquemas as e
from transparencia.db.models import (
    CatalogVersion,
    CrawlRun,
    Criterion,
    CriterionResult,
    Entity,
    EvaluationRun,
    Job,
    Revisao,
    Score,
    StatusConformidade,
    StatusExecucao,
)
from transparencia.evaluation.motor import ordem_mais_recente
from transparencia.evaluation.pontuacao import calcular_nota
from transparencia.evaluation.regras import carregar_regras
from transparencia.evaluation.veredito import Veredito
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


# ------------------------------------------------------------ revisões


def _revisoes(session: Session, entity_id: int) -> dict[str, list[Revisao]]:
    """Revisões da entidade por critério, da mais antiga para a mais recente."""
    por_criterio: dict[str, list[Revisao]] = {}
    for r in session.scalars(
        select(Revisao).where(Revisao.entity_id == entity_id).order_by(Revisao.created_at)
    ):
        por_criterio.setdefault(r.criterio_codigo, []).append(r)
    # O que a MÁQUINA dizia quando a pessoa revisou: base da herança do veredito.
    ids = [r.criterion_result_id for rs in por_criterio.values() for r in rs]
    status_revisado = dict(
        session.execute(
            select(CriterionResult.id, CriterionResult.status).where(CriterionResult.id.in_(ids))
        ).all()
    )
    for rs in por_criterio.values():
        for r in rs:
            r.status_maquina_revisado = status_revisado.get(r.criterion_result_id)
    return por_criterio


def _veredito_humano(
    revisoes: list[Revisao], resultado: CriterionResult, coleta_id: int | None
) -> Revisao | None:
    """O veredito humano que vale para este resultado (o mais recente que se aplica).

    Vale se foi dado NESTA coleta, ou se a máquina continua dizendo o mesmo que
    dizia quando a pessoa revisou (o portal não mudou naquilo que a regra enxerga).
    Se a máquina mudar de conclusão numa coleta nova, o veredito deixa de valer e
    o item volta para a fila: algo mudou e merece um novo olhar humano.
    """
    return next(
        (
            r
            for r in reversed(revisoes)
            if r.status
            and (
                r.crawl_run_id == coleta_id
                or getattr(r, "status_maquina_revisado", None) == resultado.status
            )
        ),
        None,
    )


def _revisao(r: Revisao | None) -> e.Revisao | None:
    if r is None:
        return None
    return e.Revisao(
        id=r.id,
        criterio=r.criterio_codigo,
        status=r.status,
        url_indicada=r.url_indicada,
        comentario=r.comentario,
        revisor=r.revisor,
        coleta_id=r.crawl_run_id,
        criado_em=r.created_at,
    )


def _nota_revisada(
    run: EvaluationRun, revisoes: dict[str, list[Revisao]]
) -> tuple[e.Nota | None, int]:
    """Mesma fórmula da nota da máquina, trocando o status pelo veredito humano
    onde houver. Devolve (nota, quantos critérios foram revisados); nota None se
    nenhum foi."""
    itens, revisados = [], 0
    for r in run.results:
        humano = _veredito_humano(revisoes.get(r.criterion.codigo, []), r, run.crawl_run_id)
        revisados += humano is not None
        status = StatusConformidade(humano.status if humano else r.status)
        itens.append((r.criterion, Veredito(status, float(r.confianca or 0), "")))
    if not revisados:
        return None, 0
    n = calcular_nota(itens)
    nota = e.Nota(
        valor=_f(n.valor),
        valor_min=_f(n.valor_min),
        valor_max=_f(n.valor_max),
        cobertura=_f(n.cobertura),
        nivel=n.nivel,
        essenciais_ok=n.essenciais_ok,
    )
    return nota, revisados


# ------------------------------------------------------------ entidades


def _ultima_avaliacao(session: Session, entity_id: int) -> EvaluationRun | None:
    return session.scalar(
        select(EvaluationRun)
        .outerjoin(CrawlRun, CrawlRun.id == EvaluationRun.crawl_run_id)
        .where(EvaluationRun.entity_id == entity_id, EvaluationRun.status.in_(CONCLUIDAS))
        .order_by(*ordem_mais_recente())
        .options(
            selectinload(EvaluationRun.scores),
            selectinload(EvaluationRun.results).selectinload(CriterionResult.criterion),
        )
        .limit(1)
    )


def _resumo(session: Session, run: EvaluationRun | None) -> e.ResumoAvaliacao | None:
    if run is None:
        return None
    coleta = session.get(CrawlRun, run.crawl_run_id) if run.crawl_run_id else None
    revisoes = _revisoes(session, run.entity_id)
    pendentes = sum(
        r.revisao_humana
        and _veredito_humano(revisoes.get(r.criterion.codigo, []), r, run.crawl_run_id) is None
        for r in run.results
    )
    return e.ResumoAvaliacao(
        id=run.id,
        avaliado_em=run.started_at,
        coleta_id=run.crawl_run_id,
        coletado_em=coleta.started_at if coleta else None,
        versao_regras=run.versao_regras,
        nota=_nota(_geral(run)),
        nota_revisada=_nota_revisada(run, revisoes)[0],
        revisoes_pendentes=pendentes,
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


def _status_final(r: CriterionResult, revisoes: dict, coleta_id: int | None) -> str:
    """Veredito humano que se aplica, se houver; senão o status da máquina."""
    humano = _veredito_humano(revisoes.get(r.criterion.codigo, []), r, coleta_id)
    return humano.status if humano else r.status


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
    revisoes = _revisoes(session, ent.id)
    nota_revisada, revisados = _nota_revisada(run, revisoes)
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
            id=r.id,
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
            revisao=_revisao((revisoes.get(r.criterion.codigo) or [None])[-1]),
            veredito_humano=_revisao(
                _veredito_humano(revisoes.get(r.criterion.codigo, []), r, run.crawl_run_id)
            ),
            status_final=_status_final(r, revisoes, run.crawl_run_id),
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
        nota_revisada=nota_revisada,
        revisados=revisados,
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
        revisoes = _revisoes(session, ent.id)
        for r in sorted(resultados, key=lambda r: _ordem(r.criterion.codigo)):
            if _veredito_humano(revisoes.get(r.criterion.codigo, []), r, run.crawl_run_id):
                continue  # já decidido por uma pessoa nesta coleta
            ev = min(r.evidences, key=lambda ev: ev.id) if r.evidences else None
            itens.append(
                e.ItemRevisao(
                    resultado_id=r.id,
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


def registrar_revisao(session: Session, resultado_id: int, nova: e.NovaRevisao) -> e.Revisao | None:
    """Grava o que a pessoa decidiu. O resultado da máquina não é alterado."""
    resultado = session.get(CriterionResult, resultado_id)
    if resultado is None:
        return None
    run = session.get(EvaluationRun, resultado.evaluation_run_id)
    revisao = Revisao(
        entity_id=run.entity_id,
        criterio_codigo=resultado.criterion.codigo,
        criterion_result_id=resultado.id,
        crawl_run_id=run.crawl_run_id,
        status=nova.status,
        url_indicada=nova.url_indicada,
        comentario=nova.comentario,
        revisor=nova.revisor,
    )
    session.add(revisao)
    session.commit()
    return _revisao(revisao)


def listar_revisoes(session: Session, ent: Entity) -> list[e.Revisao]:
    return [_revisao(r) for revs in _revisoes(session, ent.id).values() for r in revs]


# ------------------------------------------------------------ jobs


def _job(j: Job) -> e.Job:
    return e.Job(
        id=j.id,
        tipo=j.tipo,
        entidade=(j.payload or {}).get("slug"),
        status=j.status,
        tentativas=j.tentativas,
        criado_em=j.created_at,
        concluido_em=j.finished_at,
        resultado=j.resultado,
        ultimo_erro=j.ultimo_erro.splitlines()[0] if j.ultimo_erro else None,
    )


def pedir_coleta(session: Session, ent: Entity) -> tuple[e.Job, bool]:
    """Enfileira coleta + avaliação; o worker executa (a API não espera minutos)."""
    from transparencia.fila import COLETAR_AVALIAR, enfileirar

    job, criado = enfileirar(session, COLETAR_AVALIAR, {"slug": ent.slug, "origem": "manual"})
    session.commit()
    session.refresh(job)
    return _job(job), criado


def listar_jobs(session: Session, slug: str | None = None, limite: int = 20) -> list[e.Job]:
    consulta = select(Job).order_by(Job.id.desc()).limit(limite)
    if slug:
        consulta = consulta.where(Job.payload["slug"].as_string() == slug)
    return [_job(j) for j in session.scalars(consulta)]
