"""Motor de avaliação: roda a cascata sobre uma coleta e grava o resultado.

Cascata (decisão de projeto):
1. **Aplicabilidade** (catálogo): critério que não vale para o tipo de entidade
   vira NAO_APLICAVEL e sai da nota.
2. **Regras determinísticas** (`avaliadores.py`).
3. **IA** só na zona cinzenta (etapa 3.3).
4. **Validação:** todo veredito precisa de evidência; confiança baixa marca
   revisão humana.

Histórico append-only: cada execução cria um `evaluation_run` novo; nada é
sobrescrito. Uma coleta pode ser reavaliada quantas vezes for preciso
(ex.: depois de corrigir uma regra), sem acessar o site de novo.
"""

import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from transparencia.catalog.applicability import padrao_casa
from transparencia.catalog.schema import Esfera, Poder
from transparencia.db.models import (
    CatalogVersion,
    CrawlRun,
    Criterion,
    CriterionResult,
    Entity,
    EvaluationRun,
    Evidence,
    MetodoAvaliacao,
    Score,
    StatusConformidade,
    StatusExecucao,
)
from transparencia.evaluation.avaliadores import AVALIADORES
from transparencia.evaluation.ia import JuizIA
from transparencia.evaluation.pontuacao import METODOLOGIA_VERSAO, pontuar
from transparencia.evaluation.regras import RegrasAvaliacao
from transparencia.evaluation.snapshot import Snapshot, carregar_snapshot
from transparencia.evaluation.veredito import Evidencia, Veredito, nao_avaliado
from transparencia.settings import get_settings

# Versão do código dos avaliadores. Junto com a versão da rubrica, identifica a
# "régua" exata usada em cada avaliação (evaluation_runs.versao_regras).
# 0.2: correções da 1ª medição contra o golden set (evidência negativa exige fonte
# completa em 4.5/4.6/20.2; 7.1 exige os 7 campos; 18.1 com contexto LGPD e domínio
# da entidade; IA não acusa sozinha).
VERSAO_MOTOR = "0.2"


def versao_regua(regras: RegrasAvaliacao) -> str:
    """O que vai em evaluation_runs.versao_regras: rubrica + código dos avaliadores."""
    return f"{regras.versao}+motor{VERSAO_MOTOR}"


# Abaixo disto, o resultado é marcado para revisão humana.
CONFIANCA_MINIMA = 0.7


@dataclass
class ResultadoAvaliacao:
    criterio: Criterion
    veredito: Veredito


def ultima_coleta(session: Session, entity: Entity) -> CrawlRun | None:
    return session.scalar(
        select(CrawlRun)
        .where(
            CrawlRun.entity_id == entity.id,
            CrawlRun.status.in_([StatusExecucao.SUCESSO, StatusExecucao.PARCIAL]),
        )
        .order_by(CrawlRun.started_at.desc())
        .limit(1)
    )


def _aplicavel(criterio: Criterion, entity: Entity) -> bool:
    poder, esfera = Poder(entity.poder), Esfera(entity.esfera)
    return any(padrao_casa(p, poder, esfera) for p in criterio.aplica_a)


def avaliar_criterios(
    snap: Snapshot,
    entity: Entity,
    criterios: list[Criterion],
    regras: RegrasAvaliacao | None = None,
    juiz: JuizIA | None = None,
) -> list[ResultadoAvaliacao]:
    resultados = []
    for criterio in criterios:
        if not _aplicavel(criterio, entity):
            veredito = Veredito(
                StatusConformidade.NAO_APLICAVEL,
                1.0,
                f"Critério não se aplica a entidades do tipo {entity.tipo} "
                f"(aplica-se a: {', '.join(criterio.aplica_a)}).",
                metodo=MetodoAvaliacao.APLICABILIDADE,
            )
        else:
            try:
                veredito = AVALIADORES[criterio.codigo](snap)
            except Exception as e:  # um avaliador com defeito não derruba os outros
                veredito = nao_avaliado(
                    f"Erro no avaliador: {type(e).__name__}: {e}",
                    erro=traceback.format_exc()[-1500:],
                )
            # Zona cinzenta: a IA decide com base em trechos e citação verificada.
            if juiz is not None and regras is not None and juiz.precisa(criterio.codigo, veredito):
                regra = regras.criterios[criterio.codigo]
                veredito = juiz.avaliar(criterio.codigo, regra, criterio.descricao, snap, veredito)
            if not veredito.evidencias and veredito.status != StatusConformidade.NAO_AVALIADO:
                # Regra do projeto: veredito sem evidência não é aceito.
                veredito = nao_avaliado(
                    f"Veredito sem evidência descartado: {veredito.justificativa}"
                )
            if veredito.confianca < CONFIANCA_MINIMA:
                veredito.revisao_humana = True
        resultados.append(ResultadoAvaliacao(criterio, veredito))
    return resultados


def _gravar_evidencia(ev: Evidencia, result_id: int) -> Evidence:
    return Evidence(
        criterion_result_id=result_id,
        page_id=ev.page_id,
        tipo=ev.tipo,
        descricao=ev.descricao,
        url=ev.url,
        titulo_pagina=ev.titulo_pagina,
        trecho=ev.trecho,
        seletor=ev.seletor,
        arquivo_path=ev.arquivo_path,
        metadados=ev.metadados,
    )


def executar_avaliacao(
    session: Session,
    entity: Entity,
    regras: RegrasAvaliacao,
    catalog_version: CatalogVersion,
    crawl_run: CrawlRun | None = None,
    origem: str = "manual",
    juiz: JuizIA | None = None,
) -> tuple[EvaluationRun, list[ResultadoAvaliacao]]:
    crawl_run = crawl_run or ultima_coleta(session, entity)
    if crawl_run is None:
        raise ValueError(f"{entity.slug}: nenhuma coleta concluída para avaliar")

    run = EvaluationRun(
        entity_id=entity.id,
        crawl_run_id=crawl_run.id,
        catalog_version_id=catalog_version.id,
        versao_regras=versao_regua(regras),
        origem=origem,
        status=StatusExecucao.EXECUTANDO,
        started_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()

    criterios = session.scalars(
        select(Criterion)
        .where(
            Criterion.catalog_version_id == catalog_version.id,
            Criterion.codigo.in_(regras.codigos()),
        )
        .order_by(Criterion.ordem)
    ).all()
    snap = carregar_snapshot(session, crawl_run, get_settings().data_dir)
    resultados = avaliar_criterios(snap, entity, criterios, regras, juiz)

    custo = Decimal("0")
    for r in resultados:
        v = r.veredito
        linha = CriterionResult(
            evaluation_run_id=run.id,
            criterion_id=r.criterio.id,
            status=v.status,
            valor=v.valor,
            confianca=Decimal(str(round(v.confianca, 3))),
            metodo=v.metodo,
            justificativa=v.justificativa,
            detalhes=v.detalhes or None,
            llm_modelo=v.llm_modelo,
            llm_cache_key=v.llm_cache_key,
            revisao_humana=v.revisao_humana,
        )
        session.add(linha)
        session.flush()
        session.add_all(_gravar_evidencia(ev, linha.id) for ev in v.evidencias)
        custo += Decimal(str(v.custo_llm_usd))

    # Pontuação: uma linha 'geral' + uma por categoria. O escopo da PoC (19 de 142
    # critérios) fica registrado: a nota vale para os critérios com regra.
    total_catalogo = len(catalog_version.criteria)
    for nota in pontuar([(r.criterio, r.veredito) for r in resultados]):
        session.add(
            Score(
                evaluation_run_id=run.id,
                escopo=nota.escopo,
                categoria_codigo=nota.categoria_codigo,
                valor=nota.valor,
                valor_min=nota.valor_min,
                valor_max=nota.valor_max,
                cobertura=nota.cobertura,
                nivel=nota.nivel,
                essenciais_ok=nota.essenciais_ok,
                metodologia_versao=METODOLOGIA_VERSAO,
                detalhes={
                    **nota.detalhes,
                    "criterios_no_escopo": len(resultados),
                    "criterios_no_catalogo": total_catalogo,
                },
            )
        )

    falhas = [r for r in resultados if "Erro no avaliador" in r.veredito.justificativa]
    run.status = StatusExecucao.PARCIAL if falhas else StatusExecucao.SUCESSO
    run.erro = "; ".join(f"{r.criterio.codigo}: {r.veredito.justificativa}" for r in falhas) or None
    run.custo_llm_usd = custo
    run.finished_at = datetime.now(UTC)
    session.flush()
    return run, resultados
