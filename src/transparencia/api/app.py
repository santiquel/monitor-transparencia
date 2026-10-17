"""API HTTP do monitor (FastAPI).

Rodar:  uv run transparencia api            (ou: uv run uvicorn transparencia.api.app:app)
Docs:   http://localhost:8000/docs           (gerada automaticamente a partir dos esquemas)

A API quase só LÊ. As duas únicas escritas são leves e não apagam nada:
- registrar uma revisão humana (POST /results/{id}/review);
- PEDIR uma coleta (POST /entities/{slug}/jobs): só põe um job na fila; quem
  coleta é o worker (`transparencia worker`), fora da requisição HTTP.
Na PoC não há autenticação; em produção, essas duas rotas exigiriam login e o
revisor viria da identidade autenticada (docs/documentacao_tecnica.md, seção 10).
"""

import mimetypes
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from transparencia.api import consultas
from transparencia.api import esquemas as e
from transparencia.db.models import Evidence
from transparencia.db.session import get_engine
from transparencia.settings import get_settings

app = FastAPI(
    title="Monitor de Transparência",
    version="0.1.0",
    description=(
        "Conformidade de portais de transparência municipais com o checklist da LAI/LRF. "
        "Cada resultado traz evidências (URL, trecho literal, seletor, snapshot) e todo o "
        "histórico é preservado. Metodologia: docs/documentacao_tecnica.md."
    ),
)


def sessao() -> Iterator[Session]:
    """Uma sessão por requisição, fechada ao final (dependência do FastAPI)."""
    with sessionmaker(bind=get_engine())() as session:
        yield session


Sessao = Annotated[Session, Depends(sessao)]


def _entidade_ou_404(session: Session, slug: str):
    ent = consultas.buscar_entidade(session, slug)
    if ent is None:
        raise HTTPException(404, f"Entidade '{slug}' não encontrada.")
    return ent


@app.get("/", include_in_schema=False)
def raiz():
    return RedirectResponse("/docs")


@app.get("/health", tags=["sistema"])
def health(session: Sessao) -> dict:
    """O serviço está de pé e alcança o banco?"""
    session.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/entities", tags=["entidades"])
def entidades(session: Sessao) -> list[e.Entidade]:
    """Entidades monitoradas, com o resumo da avaliação mais recente."""
    return consultas.listar_entidades(session)


@app.get("/entities/{slug}", tags=["entidades"])
def entidade(slug: str, session: Sessao) -> e.Entidade:
    return consultas.detalhar_entidade(session, _entidade_ou_404(session, slug).slug)


@app.get("/entities/{slug}/evaluations/latest", tags=["avaliações"])
def ultima_avaliacao(slug: str, session: Sessao) -> e.Avaliacao:
    """Avaliação mais recente: nota (com faixa e cobertura), categorias e cada
    critério com status, justificativa e evidências."""
    run_id = consultas.ultima_avaliacao_id(session, _entidade_ou_404(session, slug))
    if run_id is None:
        raise HTTPException(404, f"'{slug}' ainda não foi avaliada.")
    return consultas.detalhar_avaliacao(session, run_id)


@app.get("/evaluations/{avaliacao_id}", tags=["avaliações"])
def avaliacao(avaliacao_id: int, session: Sessao) -> e.Avaliacao:
    """Qualquer avaliação do histórico (o histórico é append-only: nada é sobrescrito)."""
    resultado = consultas.detalhar_avaliacao(session, avaliacao_id)
    if resultado is None:
        raise HTTPException(404, f"Avaliação {avaliacao_id} não encontrada.")
    return resultado


@app.get("/entities/{slug}/history", tags=["avaliações"])
def historico(
    slug: str,
    session: Sessao,
    todas: Annotated[
        bool,
        Query(description="Inclui reavaliações da mesma coleta (auditoria de metodologia)"),
    ] = False,
) -> e.Historico:
    """Série de notas por coleta e os critérios que mudaram (corrigido, regrediu...)."""
    return consultas.historico(session, _entidade_ou_404(session, slug), todas=todas)


@app.get("/criteria", tags=["catálogo"])
def criterios(
    session: Sessao,
    poc: Annotated[bool, Query(description="Só os 19 critérios com regra na PoC")] = False,
) -> list[e.Criterio]:
    """Catálogo dos 142 critérios (versão mais recente), derivado do checklist oficial."""
    return consultas.listar_criterios(session, so_poc=poc)


@app.get("/review", tags=["revisão"])
def fila_de_revisao(session: Sessao) -> list[e.ItemRevisao]:
    """Critérios que precisam de um humano, na avaliação mais recente de cada entidade.
    Itens que já receberam veredito humano nesta coleta saem da fila."""
    return consultas.fila_de_revisao(session)


@app.post("/results/{resultado_id}/review", tags=["revisão"], status_code=201)
def revisar(resultado_id: int, nova: e.NovaRevisao, session: Sessao) -> e.Revisao:
    """Registra o veredito humano e/ou a URL correta de um critério.

    O resultado da máquina não muda (histórico append-only). O veredito vale para
    a coleta revisada e entra na `nota_revisada`; a URL indicada vira semente das
    próximas coletas da entidade, já marcada com o tema do critério.
    """
    revisao = consultas.registrar_revisao(session, resultado_id, nova)
    if revisao is None:
        raise HTTPException(404, f"Resultado {resultado_id} não encontrado.")
    return revisao


@app.get("/entities/{slug}/reviews", tags=["revisão"])
def revisoes(slug: str, session: Sessao) -> list[e.Revisao]:
    """Todas as revisões registradas para a entidade (auditoria)."""
    return consultas.listar_revisoes(session, _entidade_ou_404(session, slug))


@app.post("/entities/{slug}/jobs", tags=["fila"], status_code=202)
def pedir_coleta(slug: str, session: Sessao, response: Response) -> e.Job:
    """Pede uma nova coleta + avaliação. Responde na hora (202 = aceito): quem executa
    é o worker. Se já houver um pedido pendente para a entidade, devolve esse (200)."""
    job, criado = consultas.pedir_coleta(session, _entidade_ou_404(session, slug))
    if not criado:
        response.status_code = 200
    return job


@app.get("/jobs", tags=["fila"])
def jobs(
    session: Sessao,
    slug: Annotated[str | None, Query(description="Filtra por entidade")] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 20,
) -> list[e.Job]:
    """Jobs mais recentes da fila, com status, tentativas e resultado."""
    return consultas.listar_jobs(session, slug, limite)


@app.get("/evidencias/{evidencia_id}/arquivo", tags=["avaliações"])
def arquivo_da_evidencia(evidencia_id: int, session: Sessao) -> FileResponse:
    """O arquivo guardado de uma evidência: snapshot HTML/PDF ou screenshot.

    Só serve arquivos REGISTRADOS numa evidência (nunca um caminho livre) e,
    por segurança, dentro da pasta de dados. HTML de terceiros vai com CSP
    `sandbox`: o navegador mostra a página, mas não executa os scripts dela.
    """
    ev = session.get(Evidence, evidencia_id)
    if ev is None or not ev.arquivo_path:
        raise HTTPException(404, "Evidência sem arquivo.")
    base = get_settings().data_dir.resolve()
    caminho = (base / ev.arquivo_path).resolve()
    if not caminho.is_relative_to(base) or not caminho.is_file():
        raise HTTPException(404, "Arquivo da evidência não está disponível neste servidor.")
    tipo = mimetypes.guess_type(caminho.name)[0] or "application/octet-stream"
    return FileResponse(caminho, media_type=tipo, headers={"Content-Security-Policy": "sandbox"})
