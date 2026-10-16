"""Fila de trabalho no próprio PostgreSQL (tabela `jobs`) e o worker que a consome.

Por que no banco, sem Redis/RabbitMQ: na escala do problema (5.000 portais/mês
≈ 170 coletas/dia) o PostgreSQL basta, e é uma peça a menos para operar.

Como funciona:
- **Enfileirar** grava uma linha PENDENTE. Quem enfileira (API, agendador, CLI)
  não espera: a coleta leva minutos e roda no worker.
- **Pegar o próximo** usa `SELECT ... FOR UPDATE SKIP LOCKED`: a linha fica
  travada pela transação de quem a pegou, e outros workers simplesmente a
  PULAM. Vários workers em paralelo nunca pegam o mesmo job.
- **Falha** volta para PENDENTE com espera crescente (1, 2, 4... min) até
  `max_tentativas`; depois vira FALHA, com o erro guardado.
- **Worker que morreu** (máquina desligada no meio): um job EXECUTANDO há mais
  de `TRAVADO_APOS` volta para a fila.
"""

import os
import socket
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from transparencia.db.models import Job

COLETAR_AVALIAR = "coletar_avaliar"
ATIVOS = ("PENDENTE", "EXECUTANDO")
TRAVADO_APOS = timedelta(hours=2)


def agora() -> datetime:
    return datetime.now(UTC)


def nome_do_worker() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def enfileirar(
    session: Session, tipo: str, payload: dict, quando: datetime | None = None
) -> tuple[Job, bool]:
    """Cria o job, ou devolve o que já está na fila com o mesmo pedido.

    Devolve (job, criado). Evita duplicar: clicar duas vezes em "recoletar" não
    gera duas coletas da mesma entidade.
    """
    # Mesmo pedido = mesmo tipo e mesma entidade (a origem pode diferir: agendada x manual).
    mesmo = (
        Job.payload["slug"].as_string() == payload["slug"]
        if "slug" in payload
        else Job.payload == payload
    )
    existente = session.scalar(
        select(Job).where(Job.tipo == tipo, mesmo, Job.status.in_(ATIVOS)).limit(1)
    )
    if existente:
        return existente, False
    job = Job(tipo=tipo, payload=payload, status="PENDENTE", run_after=quando or agora())
    session.add(job)
    session.flush()
    return job, True


def pegar_proximo(session: Session, worker: str) -> Job | None:
    """Trava e marca como EXECUTANDO o próximo job pronto (ou devolve None)."""
    limite_travado = agora() - TRAVADO_APOS
    job = session.scalar(
        select(Job)
        .where(
            or_(
                and_(Job.status == "PENDENTE", Job.run_after <= agora()),
                and_(Job.status == "EXECUTANDO", Job.locked_at < limite_travado),
            )
        )
        .order_by(Job.run_after, Job.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    job.status = "EXECUTANDO"
    job.locked_at = agora()
    job.locked_by = worker
    job.tentativas += 1
    session.commit()  # libera a trava da linha; o status EXECUTANDO segura os outros
    return job


def concluir(session: Session, job: Job, resultado: dict) -> None:
    job.status = "SUCESSO"
    job.resultado = resultado
    job.finished_at = agora()
    job.ultimo_erro = None
    session.commit()


def falhar(session: Session, job: Job, erro: str) -> None:
    job.ultimo_erro = erro[-2000:]
    if job.tentativas < job.max_tentativas:
        job.status = "PENDENTE"
        job.run_after = agora() + timedelta(minutes=2 ** (job.tentativas - 1))
        job.locked_at = job.locked_by = None
    else:
        job.status = "FALHA"
        job.finished_at = agora()
    session.commit()


# ------------------------------------------------------------ execução


def coletar_e_avaliar(session: Session, payload: dict) -> dict:
    """Coleta a entidade (com as URLs indicadas nas revisões) e avalia a coleta nova."""
    from transparencia.catalog.loader import carregar_catalogo, carregar_entidades
    from transparencia.collector.coletor import ParametrosColeta
    from transparencia.collector.execucao import executar_coleta
    from transparencia.db.models import CatalogVersion, Entity
    from transparencia.evaluation.ia import criar_juiz
    from transparencia.evaluation.motor import executar_avaliacao
    from transparencia.evaluation.regras import carregar_regras
    from transparencia.settings import get_settings

    s = get_settings()
    slug = payload["slug"]
    entity = session.scalar(select(Entity).where(Entity.slug == slug))
    if entity is None:
        raise ValueError(f"entidade '{slug}' não existe no banco")
    cadastro = {e.slug: e for e in carregar_entidades(s.entities_path).entidades}
    params = ParametrosColeta(
        ignorar_robots=cadastro[slug].ignorar_robots if slug in cadastro else False
    )
    coleta = executar_coleta(session, entity, params)
    if coleta.status == "FALHA":
        raise RuntimeError(f"coleta {coleta.id} falhou: {coleta.erro}")

    regras = carregar_regras(s.evaluation_rules_path)
    hash_catalogo = carregar_catalogo(s.criteria_catalog_path).content_hash()
    versao = session.scalar(
        select(CatalogVersion).where(CatalogVersion.content_hash == hash_catalogo)
    )
    juiz = criar_juiz(s, regras.versao, avisar=lambda _m: None)
    origem = payload.get("origem", "manual")
    avaliacao, _ = executar_avaliacao(
        session, entity, regras, versao, coleta, origem=origem, juiz=juiz
    )
    session.commit()
    return {"crawl_run_id": coleta.id, "evaluation_run_id": avaliacao.id}


EXECUTORES: dict[str, Callable[[Session, dict], dict]] = {COLETAR_AVALIAR: coletar_e_avaliar}


def processar_um(
    session: Session, worker: str, executores: dict | None = None, log=print
) -> Job | None:
    """Pega UM job, executa e registra o desfecho. Devolve o job (ou None se a fila está vazia)."""
    executores = executores or EXECUTORES
    job = pegar_proximo(session, worker)
    if job is None:
        return None
    log(f"job {job.id} ({job.tipo} {job.payload}) - tentativa {job.tentativas}")
    try:
        executor = executores.get(job.tipo)
        if executor is None:
            raise ValueError(f"tipo de job desconhecido: {job.tipo}")
        resultado = executor(session, job.payload)
    except Exception as e:  # o worker não pode morrer por causa de um job
        session.rollback()
        falhar(session, job, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
        log(f"job {job.id}: erro ({type(e).__name__}: {e}) -> {job.status}")
    else:
        concluir(session, job, resultado)
        log(f"job {job.id}: SUCESSO {resultado}")
    return job


def rodar_worker(fabrica_sessao, uma_vez: bool = False, intervalo_s: float = 10, log=print):
    """Laço do worker: processa até esvaziar; se `uma_vez`, para; senão espera e repete."""
    worker = nome_do_worker()
    log(f"worker {worker} iniciado")
    while True:
        with fabrica_sessao() as session:
            processou = processar_um(session, worker, log=log)
        if processou is None:
            if uma_vez:
                log("fila vazia: encerrando")
                return
            time.sleep(intervalo_s)
