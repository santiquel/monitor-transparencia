"""Executa uma coleta e grava o resultado no banco (`crawl_runs`, `pages`, `entity_urls`).

Cada página é gravada (commit) assim que coletada: se a coleta cair no meio,
o que já foi visto fica registrado, e o `crawl_run` mostra até onde chegou.
"""

import traceback
from dataclasses import asdict
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from transparencia.collector.armazenamento import Armazenamento
from transparencia.collector.buscadores import Buscador, BuscadorHttp, BuscadorNavegador
from transparencia.collector.coletor import (
    Coletor,
    PaginaColetada,
    ParametrosColeta,
    ResultadoColeta,
)
from transparencia.collector.robots import PoliticaRobots
from transparencia.db.models import CrawlRun, Entity, EntityUrl, Page, StatusExecucao
from transparencia.settings import get_settings

CAMPOS_PAGE = (
    "url",
    "url_final",
    "url_origem",
    "texto_link",
    "profundidade",
    "http_status",
    "content_type",
    "titulo",
    "modo_render",
    "tamanho_bytes",
    "content_hash",
    "storage_path",
    "screenshot_path",
    "fetched_at",
    "erro",
)


def urls_ativas(session: Session, entity: Entity) -> dict[str, str]:
    """{'site': url, 'portal': url} — a URL ativa mais recente de cada tipo."""
    linhas = session.scalars(
        select(EntityUrl)
        .where(EntityUrl.entity_id == entity.id, EntityUrl.ativo.is_(True))
        .order_by(EntityUrl.first_seen_at)
    )
    return {u.tipo: u.url for u in linhas}


def _registrar_portal(
    session: Session, entity: Entity, resultado: ResultadoColeta, url_portal: str | None
) -> None:
    agora = datetime.now(UTC)
    url = url_portal or resultado.portal_descoberto
    if url is None:
        return
    existente = session.scalar(
        select(EntityUrl).where(
            EntityUrl.entity_id == entity.id, EntityUrl.tipo == "portal", EntityUrl.url == url
        )
    )
    if existente is None:  # só acontece com portal descoberto
        existente = EntityUrl(entity_id=entity.id, tipo="portal", url=url, origem="descoberta")
        session.add(existente)
    existente.last_seen_at = agora
    existente.fornecedor = resultado.fornecedor or existente.fornecedor


def executar_coleta(
    session: Session,
    entity: Entity,
    params: ParametrosColeta,
    *,
    cliente: httpx.Client | None = None,
    navegador: Buscador | None = None,
    ao_coletar=None,
) -> CrawlRun:
    """Coleta uma entidade. `cliente`/`navegador` podem ser trocados nos testes."""
    s = get_settings()
    urls = urls_ativas(session, entity)
    url_site, url_portal = urls["site"], urls.get("portal")

    run = CrawlRun(
        entity_id=entity.id,
        url_inicial=url_portal or url_site,
        status=StatusExecucao.EXECUTANDO,
        started_at=datetime.now(UTC),
        parametros={**asdict(params), "user_agent": s.coletor_user_agent, "url_site": url_site},
    )
    session.add(run)
    session.commit()

    def gravar(p: PaginaColetada) -> None:
        session.add(Page(crawl_run_id=run.id, **{c: getattr(p, c) for c in CAMPOS_PAGE}))
        run.paginas_coletadas += int(p.ok)
        run.paginas_com_erro += int(not p.ok)
        session.commit()
        if ao_coletar:
            ao_coletar(p)

    cliente_proprio = cliente is None
    cliente = cliente or httpx.Client(
        headers={"User-Agent": s.coletor_user_agent},
        timeout=params.timeout_s,
        follow_redirects=True,
    )
    if navegador is None and params.usar_navegador:
        navegador = BuscadorNavegador(s.coletor_user_agent, params.timeout_s)

    try:
        coletor = Coletor(
            params,
            http=BuscadorHttp(cliente),
            robots=PoliticaRobots(cliente, s.coletor_user_agent, ignorar=params.ignorar_robots),
            armazenamento=Armazenamento(s.data_dir),
            navegador=navegador,
            ao_coletar=gravar,
        )
        resultado = coletor.coletar(url_site, url_portal)

        run.fornecedor_detectado = resultado.fornecedor
        _registrar_portal(session, entity, resultado, url_portal)
        if resultado.coletadas == 0:
            run.status = StatusExecucao.FALHA
        elif resultado.com_erro or resultado.avisos:
            run.status = StatusExecucao.PARCIAL
        else:
            run.status = StatusExecucao.SUCESSO
        run.erro = "; ".join(resultado.avisos) or None
    except KeyboardInterrupt:  # Ctrl+C: registra a interrupção e deixa o programa parar
        session.rollback()
        run.status = StatusExecucao.FALHA
        run.erro = "coleta interrompida pelo usuário"
        raise
    except Exception:
        session.rollback()
        run.status = StatusExecucao.FALHA
        run.erro = traceback.format_exc()[-2000:]
    finally:
        run.finished_at = datetime.now(UTC)
        session.commit()
        if cliente_proprio:
            cliente.close()
        if isinstance(navegador, BuscadorNavegador):
            navegador.fechar()
    return run
