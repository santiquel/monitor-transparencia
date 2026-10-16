"""Modelo de dados.

Modelos SQLAlchemy das tabelas do PostgreSQL (uma classe por tabela; o esquema
em SQL está em `migrations/sql/`). Visão geral: `docs/documentacao_tecnica.md`, seção 2.

Princípios do modelo
--------------------
1. **Histórico append-only**: avaliações nunca são sobrescritas; cada execução
   gera novas linhas. Assim respondemos "quando o critério 4.1 foi corrigido?".
2. **Separação coleta × avaliação**: `crawl_runs`/`pages` guardam o que foi visto;
   `evaluation_runs`/`criterion_results` guardam o que foi concluído. Dá para
   reavaliar uma coleta antiga com regras novas sem acessar o site de novo.
3. **Reprodutibilidade**: cada avaliação aponta para a versão exata do catálogo
   de critérios usado (`catalog_versions`).
4. **Explicabilidade**: todo resultado pode ter N evidências (URL, trecho,
   seletor, arquivo, screenshot).
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSONB no PostgreSQL (indexável e eficiente); JSON genérico em outros bancos.
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Vocabulários controlados (gravados como texto + CHECK constraint no banco)
# ---------------------------------------------------------------------------


class StatusConformidade(StrEnum):
    ATENDE = "ATENDE"
    ATENDE_PARCIALMENTE = "ATENDE_PARCIALMENTE"
    NAO_ATENDE = "NAO_ATENDE"
    NAO_APLICAVEL = "NAO_APLICAVEL"  # critério não pertence ao tipo da entidade
    NAO_AVALIADO = "NAO_AVALIADO"  # sistema não conseguiu concluir (site fora, etc.)


class StatusExecucao(StrEnum):
    PENDENTE = "PENDENTE"
    EXECUTANDO = "EXECUTANDO"
    SUCESSO = "SUCESSO"
    PARCIAL = "PARCIAL"  # terminou, mas parte das páginas/critérios falhou
    FALHA = "FALHA"


class MetodoAvaliacao(StrEnum):
    REGRA = "REGRA"
    LLM = "LLM"
    REGRA_LLM = "REGRA_LLM"
    MANUAL = "MANUAL"  # rótulo humano (golden set / revisão)
    APLICABILIDADE = "APLICABILIDADE"  # NAO_APLICAVEL decidido pelo catálogo


def _check_in(coluna: str, enum: type[StrEnum]) -> str:
    valores = ", ".join(f"'{v.value}'" for v in enum)
    return f"{coluna} IN ({valores})"


# ---------------------------------------------------------------------------
# Cadastro
# ---------------------------------------------------------------------------


class Entity(TimestampMixin, Base):
    """Entidade pública avaliada (ex.: Prefeitura de Japeri)."""

    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    nome: Mapped[str] = mapped_column(String(200))
    municipio: Mapped[str] = mapped_column(String(120))
    uf: Mapped[str] = mapped_column(String(2))
    codigo_ibge: Mapped[str | None] = mapped_column(String(7))
    poder: Mapped[str] = mapped_column(String(20))
    esfera: Mapped[str] = mapped_column(String(20))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    notas: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    urls: Mapped[list["EntityUrl"]] = relationship(back_populates="entity")

    @property
    def tipo(self) -> str:
        return f"{self.poder}_{self.esfera}"


class EntityUrl(TimestampMixin, Base):
    """Endereços da entidade ao longo do tempo (portais mudam de URL e de fornecedor)."""

    __tablename__ = "entity_urls"
    __table_args__ = (
        UniqueConstraint("entity_id", "tipo", "url"),
        CheckConstraint("tipo IN ('site', 'portal')", name="ck_entity_urls_tipo"),
        CheckConstraint("origem IN ('cadastro', 'descoberta')", name="ck_entity_urls_origem"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    tipo: Mapped[str] = mapped_column(String(10))
    url: Mapped[str] = mapped_column(Text)
    origem: Mapped[str] = mapped_column(String(12))
    fornecedor: Mapped[str | None] = mapped_column(String(80))  # ex.: "GeoSiap", "e-cidade"
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    entity: Mapped[Entity] = relationship(back_populates="urls")


# ---------------------------------------------------------------------------
# Catálogo de critérios (versionado)
# ---------------------------------------------------------------------------


class CatalogVersion(TimestampMixin, Base):
    __tablename__ = "catalog_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    versao: Mapped[str] = mapped_column(String(40))
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    fonte: Mapped[str] = mapped_column(String(200))
    pesos_classificacao: Mapped[dict] = mapped_column(JSONType)

    criteria: Mapped[list["Criterion"]] = relationship(back_populates="catalog_version")


class Criterion(Base):
    __tablename__ = "criteria"
    __table_args__ = (
        UniqueConstraint("catalog_version_id", "codigo"),
        CheckConstraint(
            "classificacao IN ('essencial', 'obrigatorio', 'recomendado')",
            name="ck_criteria_classificacao",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    catalog_version_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_versions.id", ondelete="CASCADE")
    )
    codigo: Mapped[str] = mapped_column(String(10))  # "4.1"
    categoria_codigo: Mapped[str] = mapped_column(String(5))  # "4"
    categoria_nome: Mapped[str] = mapped_column(String(200))
    bloco: Mapped[str] = mapped_column(String(40))
    matriz: Mapped[str] = mapped_column(String(40))
    descricao: Mapped[str] = mapped_column(Text)
    fundamentacao: Mapped[str | None] = mapped_column(Text)
    dimensao: Mapped[str] = mapped_column(String(30))
    classificacao: Mapped[str] = mapped_column(String(20))
    peso: Mapped[Decimal] = mapped_column(Numeric(4, 2))
    aplica_a: Mapped[list[str]] = mapped_column(ARRAY(String(40)))
    condicional: Mapped[bool] = mapped_column(Boolean, default=False)
    nota: Mapped[str | None] = mapped_column(Text)
    # Ordem natural (1.1 < 1.2 < 1.10) — strings ordenariam "1.10" antes de "1.2".
    ordem: Mapped[int] = mapped_column(Integer)

    catalog_version: Mapped[CatalogVersion] = relationship(back_populates="criteria")


# ---------------------------------------------------------------------------
# Coleta (o que o robô VIU)
# ---------------------------------------------------------------------------


class CrawlRun(TimestampMixin, Base):
    __tablename__ = "crawl_runs"
    __table_args__ = (
        CheckConstraint(_check_in("status", StatusExecucao), name="ck_crawl_runs_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    url_inicial: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default=StatusExecucao.PENDENTE)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paginas_coletadas: Mapped[int] = mapped_column(Integer, default=0)
    paginas_com_erro: Mapped[int] = mapped_column(Integer, default=0)
    fornecedor_detectado: Mapped[str | None] = mapped_column(String(80))
    erro: Mapped[str | None] = mapped_column(Text)
    parametros: Mapped[dict | None] = mapped_column(JSONType)  # profundidade, limites...

    pages: Mapped[list["Page"]] = relationship(back_populates="crawl_run")


class Page(TimestampMixin, Base):
    """Snapshot de uma página/arquivo. O conteúdo fica em disco; aqui, os metadados."""

    __tablename__ = "pages"
    __table_args__ = (
        Index("ix_pages_content_hash", "content_hash"),
        Index("ix_pages_crawl_url", "crawl_run_id", "url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    crawl_run_id: Mapped[int] = mapped_column(ForeignKey("crawl_runs.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(Text)
    url_final: Mapped[str | None] = mapped_column(Text)  # após redirecionamentos
    url_origem: Mapped[str | None] = mapped_column(Text)  # página que linkou esta
    texto_link: Mapped[str | None] = mapped_column(Text)  # texto do link clicado
    profundidade: Mapped[int] = mapped_column(Integer, default=0)
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(120))
    titulo: Mapped[str | None] = mapped_column(Text)
    modo_render: Mapped[str | None] = mapped_column(String(10))  # 'http' | 'browser'
    tamanho_bytes: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str | None] = mapped_column(String(64))  # sha256 do conteúdo
    storage_path: Mapped[str | None] = mapped_column(Text)  # caminho relativo em data/
    screenshot_path: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    erro: Mapped[str | None] = mapped_column(Text)

    crawl_run: Mapped[CrawlRun] = relationship(back_populates="pages")


# ---------------------------------------------------------------------------
# Avaliação (o que o sistema CONCLUIU)
# ---------------------------------------------------------------------------


class EvaluationRun(TimestampMixin, Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        CheckConstraint(_check_in("status", StatusExecucao), name="ck_eval_runs_status"),
        CheckConstraint(
            "origem IN ('agendada', 'manual', 'reprocessamento')", name="ck_eval_runs_origem"
        ),
        Index("ix_eval_runs_entity_started", "entity_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    crawl_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("crawl_runs.id", ondelete="SET NULL")
    )
    catalog_version_id: Mapped[int] = mapped_column(ForeignKey("catalog_versions.id"))
    versao_regras: Mapped[str] = mapped_column(String(40))  # versão do motor de avaliação
    origem: Mapped[str] = mapped_column(String(20), default="manual")
    status: Mapped[str] = mapped_column(String(12), default=StatusExecucao.PENDENTE)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    erro: Mapped[str | None] = mapped_column(Text)
    custo_llm_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))

    results: Mapped[list["CriterionResult"]] = relationship(back_populates="evaluation_run")
    scores: Mapped[list["Score"]] = relationship(back_populates="evaluation_run")


class CriterionResult(TimestampMixin, Base):
    __tablename__ = "criterion_results"
    __table_args__ = (
        UniqueConstraint("evaluation_run_id", "criterion_id"),
        CheckConstraint(_check_in("status", StatusConformidade), name="ck_results_status"),
        CheckConstraint(_check_in("metodo", MetodoAvaliacao), name="ck_results_metodo"),
        CheckConstraint("confianca BETWEEN 0 AND 1", name="ck_results_confianca"),
        CheckConstraint("valor IN (0, 0.5, 1)", name="ck_results_valor"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_run_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE")
    )
    criterion_id: Mapped[int] = mapped_column(ForeignKey("criteria.id"))
    status: Mapped[str] = mapped_column(String(25))
    # 1 = atende, 0.5 = parcial, 0 = não atende, NULL = não aplicável/não avaliado
    valor: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    confianca: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    metodo: Mapped[str] = mapped_column(String(20))
    justificativa: Mapped[str | None] = mapped_column(Text)
    detalhes: Mapped[dict | None] = mapped_column(JSONType)  # sinais usados, scores parciais
    llm_modelo: Mapped[str | None] = mapped_column(String(60))
    llm_cache_key: Mapped[str | None] = mapped_column(String(64))
    revisao_humana: Mapped[bool] = mapped_column(Boolean, default=False)

    evaluation_run: Mapped[EvaluationRun] = relationship(back_populates="results")
    criterion: Mapped[Criterion] = relationship()
    evidences: Mapped[list["Evidence"]] = relationship(back_populates="result")


class Evidence(TimestampMixin, Base):
    __tablename__ = "evidences"
    __table_args__ = (
        CheckConstraint(
            "tipo IN ('link', 'texto', 'tabela', 'formulario', 'documento', "
            "'arquivo_dados', 'screenshot', 'metadado', 'elemento')",
            name="ck_evidences_tipo",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    criterion_result_id: Mapped[int] = mapped_column(
        ForeignKey("criterion_results.id", ondelete="CASCADE")
    )
    page_id: Mapped[int | None] = mapped_column(ForeignKey("pages.id", ondelete="SET NULL"))
    tipo: Mapped[str] = mapped_column(String(20))
    descricao: Mapped[str] = mapped_column(Text)  # frase legível por humano
    url: Mapped[str | None] = mapped_column(Text)
    titulo_pagina: Mapped[str | None] = mapped_column(Text)
    trecho: Mapped[str | None] = mapped_column(Text)  # citação literal do conteúdo
    seletor: Mapped[str | None] = mapped_column(Text)  # CSS/XPath do elemento
    arquivo_path: Mapped[str | None] = mapped_column(Text)  # PDF/CSV/screenshot em data/
    metadados: Mapped[dict | None] = mapped_column(JSONType)

    result: Mapped[CriterionResult] = relationship(back_populates="evidences")


class Score(TimestampMixin, Base):
    """Pontuação de uma avaliação: uma linha 'geral' + uma por categoria."""

    __tablename__ = "scores"
    __table_args__ = (
        UniqueConstraint(
            "evaluation_run_id",
            "escopo",
            "categoria_codigo",
            postgresql_nulls_not_distinct=True,  # só uma linha 'geral' por avaliação
        ),
        CheckConstraint("escopo IN ('geral', 'categoria')", name="ck_scores_escopo"),
        CheckConstraint("valor BETWEEN 0 AND 100", name="ck_scores_valor"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_run_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE")
    )
    escopo: Mapped[str] = mapped_column(String(10))
    categoria_codigo: Mapped[str | None] = mapped_column(String(5))
    valor: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # 0–100
    valor_min: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # não avaliados = 0
    valor_max: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # não avaliados = 1
    cobertura: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # % avaliado
    nivel: Mapped[str | None] = mapped_column(String(20))  # Diamante, Ouro, ...
    essenciais_ok: Mapped[bool | None] = mapped_column(Boolean)
    metodologia_versao: Mapped[str] = mapped_column(String(20))
    detalhes: Mapped[dict | None] = mapped_column(JSONType)

    evaluation_run: Mapped[EvaluationRun] = relationship(back_populates="scores")


# ---------------------------------------------------------------------------
# Fila de trabalho (processamento assíncrono sem infraestrutura extra)
# ---------------------------------------------------------------------------


class Job(TimestampMixin, Base):
    """Fila no próprio PostgreSQL.

    Workers pegam tarefas com `SELECT ... FOR UPDATE SKIP LOCKED`: vários
    processos em paralelo, sem dois pegarem o mesmo job. Suficiente para
    milhares de jobs/dia; trocável por Redis/RabbitMQ/SQS se precisar.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDENTE', 'EXECUTANDO', 'SUCESSO', 'FALHA', 'CANCELADO')",
            name="ck_jobs_status",
        ),
        Index("ix_jobs_fila", "status", "run_after"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(30))  # 'coletar', 'avaliar', 'pontuar'
    payload: Mapped[dict] = mapped_column(JSONType)
    status: Mapped[str] = mapped_column(String(12), default="PENDENTE")
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    max_tentativas: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(80))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_erro: Mapped[str | None] = mapped_column(Text)
    resultado: Mapped[dict | None] = mapped_column(JSONType)  # ex.: ids da coleta/avaliação


# ---------------------------------------------------------------------------
# Revisão humana (migrations/sql/0003_revisoes.sql)
# ---------------------------------------------------------------------------


class Revisao(TimestampMixin, Base):
    """O que uma pessoa decidiu sobre um critério de uma entidade.

    Não altera o resultado da máquina: fica ao lado dele. `status` (veredito)
    vale para a coleta revisada; `url_indicada` vira semente das próximas coletas.
    """

    __tablename__ = "revisoes"
    __table_args__ = (
        CheckConstraint(
            "status IS NULL OR status IN "
            "('ATENDE', 'ATENDE_PARCIALMENTE', 'NAO_ATENDE', 'NAO_APLICAVEL')",
            name="ck_revisoes_status",
        ),
        CheckConstraint("status IS NOT NULL OR url_indicada IS NOT NULL", name="ck_revisoes_algo"),
        Index("ix_revisoes_entidade_criterio", "entity_id", "criterio_codigo", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    criterio_codigo: Mapped[str] = mapped_column(String(10))
    criterion_result_id: Mapped[int | None] = mapped_column(
        ForeignKey("criterion_results.id", ondelete="SET NULL")
    )
    crawl_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("crawl_runs.id", ondelete="SET NULL")
    )
    status: Mapped[str | None] = mapped_column(String(25))
    url_indicada: Mapped[str | None] = mapped_column(Text)
    comentario: Mapped[str | None] = mapped_column(Text)
    revisor: Mapped[str | None] = mapped_column(String(80))
