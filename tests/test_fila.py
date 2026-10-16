"""Testes da fila de jobs contra PostgreSQL de verdade (SKIP LOCKED só existe lá)."""

from datetime import timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from transparencia.db.models import Job
from transparencia.fila import (
    COLETAR_AVALIAR,
    TRAVADO_APOS,
    agora,
    enfileirar,
    pegar_proximo,
    processar_um,
)

pytestmark = pytest.mark.db


@pytest.fixture
def fabrica(engine):
    with Session(engine) as s, s.begin():
        s.execute(delete(Job))
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_enfileirar_nao_duplica_pedido_da_mesma_entidade(fabrica):
    with fabrica() as s:
        j1, criado1 = enfileirar(s, COLETAR_AVALIAR, {"slug": "japeri", "origem": "manual"})
        j2, criado2 = enfileirar(s, COLETAR_AVALIAR, {"slug": "japeri", "origem": "agendada"})
        j3, criado3 = enfileirar(s, COLETAR_AVALIAR, {"slug": "itaborai"})
        s.commit()
    assert criado1 and not criado2 and criado3
    assert j1.id == j2.id != j3.id


def test_dois_workers_nunca_pegam_o_mesmo_job(fabrica):
    with fabrica() as s:
        enfileirar(s, COLETAR_AVALIAR, {"slug": "a"})
        enfileirar(s, COLETAR_AVALIAR, {"slug": "b"})
        s.commit()
    # O worker 1 trava a linha que pegou (transação aberta, sem commit)...
    with fabrica() as s1, fabrica() as s2:
        travado = s1.scalar(select(Job).order_by(Job.id).with_for_update(skip_locked=True).limit(1))
        # ...e o worker 2, ao mesmo tempo, pula essa linha e pega a outra.
        outro = pegar_proximo(s2, "worker-2")
        assert outro is not None and outro.id != travado.id
        s1.rollback()


def test_falha_volta_para_fila_com_espera_e_depois_desiste(fabrica):
    def quebra(_s, _payload):
        raise RuntimeError("site fora do ar")

    with fabrica() as s:
        job, _ = enfileirar(s, "teste", {"slug": "x"})
        job.max_tentativas = 2
        s.commit()
        processar_um(s, "w", {"teste": quebra}, log=lambda _m: None)
        s.refresh(job)
        assert job.status == "PENDENTE" and job.tentativas == 1
        assert job.run_after > agora()  # espera antes da nova tentativa
        assert "site fora do ar" in job.ultimo_erro

        assert processar_um(s, "w", {"teste": quebra}, log=lambda _m: None) is None  # ainda não
        job.run_after = agora() - timedelta(seconds=1)
        s.commit()
        processar_um(s, "w", {"teste": quebra}, log=lambda _m: None)
        s.refresh(job)
        assert job.status == "FALHA" and job.tentativas == 2 and job.finished_at


def test_sucesso_guarda_o_resultado(fabrica):
    with fabrica() as s:
        job, _ = enfileirar(s, "teste", {"slug": "x"})
        s.commit()
        processar_um(s, "w", {"teste": lambda _s, p: {"eco": p["slug"]}}, log=lambda _m: None)
        s.refresh(job)
        assert job.status == "SUCESSO" and job.resultado == {"eco": "x"}


def test_job_de_worker_que_morreu_volta_a_ser_processado(fabrica):
    with fabrica() as s:
        job, _ = enfileirar(s, "teste", {"slug": "x"})
        job.status, job.locked_by = "EXECUTANDO", "worker-morto"
        job.locked_at = agora() - TRAVADO_APOS - timedelta(minutes=1)
        s.commit()
        retomado = pegar_proximo(s, "worker-novo")
        assert retomado.id == job.id and retomado.locked_by == "worker-novo"
