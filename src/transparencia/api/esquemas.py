"""Formato das respostas da API (Pydantic).

Estes modelos são o "contrato" da API: o FastAPI valida a saída contra eles e
os publica na documentação automática (/docs). Valores numéricos saem como
float (o banco guarda Decimal) para o JSON ficar simples de consumir.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Nota(BaseModel):
    """Pontuação de uma avaliação (geral ou de uma categoria)."""

    valor: float | None  # 0–100, sobre os critérios avaliados
    valor_min: float | None  # se todo NAO_AVALIADO fosse NAO_ATENDE
    valor_max: float | None  # se todo NAO_AVALIADO fosse ATENDE
    cobertura: float | None  # % dos critérios aplicáveis que foram avaliados
    nivel: str | None = None
    essenciais_ok: bool | None = None


class NotaCategoria(Nota):
    categoria_codigo: str
    categoria_nome: str


class ResumoAvaliacao(BaseModel):
    id: int
    avaliado_em: datetime | None
    coleta_id: int | None
    coletado_em: datetime | None
    versao_regras: str
    nota: Nota | None
    # Mesma fórmula, usando o veredito humano onde houver (None = nenhuma revisão).
    nota_revisada: Nota | None = None
    revisoes_pendentes: int


class Url(BaseModel):
    tipo: str  # 'site' | 'portal'
    url: str
    origem: str  # 'cadastro' | 'descoberta'
    fornecedor: str | None


class Entidade(BaseModel):
    slug: str
    nome: str
    municipio: str
    uf: str
    tipo: str  # ex.: executivo_municipal
    urls: list[Url]
    ultima_avaliacao: ResumoAvaliacao | None


class Evidencia(BaseModel):
    id: int
    tipo: str
    descricao: str
    url: str | None
    titulo_pagina: str | None
    trecho: str | None
    seletor: str | None
    # Rota da API que devolve o arquivo guardado (HTML do snapshot ou screenshot).
    arquivo: str | None
    metadados: dict | None


class Revisao(BaseModel):
    """O que uma pessoa registrou sobre o critério (não altera o resultado da máquina)."""

    id: int
    criterio: str
    status: str | None  # veredito humano; None = só indicou a URL
    url_indicada: str | None
    comentario: str | None
    revisor: str | None
    coleta_id: int | None  # o veredito vale para esta coleta
    criado_em: datetime


class NovaRevisao(BaseModel):
    """Entrada de POST /results/{id}/review. Pelo menos status ou url_indicada."""

    status: Literal["ATENDE", "ATENDE_PARCIALMENTE", "NAO_ATENDE", "NAO_APLICAVEL"] | None = None
    url_indicada: str | None = Field(
        None, pattern=r"^https?://", description="Página certa para o critério (vira semente)"
    )
    comentario: str | None = Field(None, max_length=2000)
    revisor: str | None = Field(None, max_length=80)

    @model_validator(mode="after")
    def _algo(self):
        if self.status is None and not self.url_indicada:
            raise ValueError("informe o veredito (status) e/ou a URL correta")
        return self


class Resultado(BaseModel):
    id: int  # usado para registrar uma revisão
    criterio: str
    descricao: str
    categoria_codigo: str
    categoria_nome: str
    classificacao: str  # essencial | obrigatorio | recomendado
    status: str
    confianca: float | None
    metodo: str
    justificativa: str | None
    revisao_humana: bool
    llm_modelo: str | None
    evidencias: list[Evidencia]
    revisao: Revisao | None = None  # a mais recente para o critério nesta entidade
    # O veredito humano que se aplica a ESTE resultado: dado nesta coleta, ou herdado
    # de uma anterior enquanto a máquina mantiver a mesma conclusão.
    veredito_humano: Revisao | None = None
    # Veredito humano que se aplica, se houver; senão o da máquina.
    status_final: str


class Avaliacao(BaseModel):
    id: int
    entidade: str
    status: str
    avaliado_em: datetime | None
    coleta_id: int | None
    coletado_em: datetime | None
    versao_regras: str
    custo_llm_usd: float | None
    nota: Nota | None
    nota_revisada: Nota | None = None
    revisados: int = 0  # critérios com veredito humano nesta coleta
    categorias: list[NotaCategoria]
    resultados: list[Resultado]


class PontoHistorico(BaseModel):
    """Uma coleta e a nota da avaliação usada para ela."""

    coleta_id: int | None
    coletado_em: datetime | None
    avaliacao_id: int
    avaliado_em: datetime | None
    versao_regras: str
    nota: Nota | None
    variacao: float | None  # diferença de nota em relação ao ponto anterior


class EventoCriterio(BaseModel):
    """Mudança de status de um critério entre dois pontos do histórico."""

    criterio: str
    descricao: str
    de: str
    para: str
    evento: str  # CORRIGIDO | MELHOROU | REGREDIU | MUDOU
    em: datetime | None
    avaliacao_id: int


class Historico(BaseModel):
    entidade: str
    regua_unica: bool  # todos os pontos usam a mesma versão das regras?
    pontos: list[PontoHistorico]
    eventos: list[EventoCriterio]


class Criterio(BaseModel):
    codigo: str
    categoria_codigo: str
    categoria_nome: str
    descricao: str
    classificacao: str
    peso: float
    dimensao: str
    aplica_a: list[str]
    na_poc: bool  # tem regra de avaliação nesta prova de conceito?


class ItemRevisao(BaseModel):
    resultado_id: int
    entidade: str
    entidade_nome: str
    avaliacao_id: int
    criterio: str
    descricao: str
    status: str
    confianca: float | None
    justificativa: str | None
    evidencia_url: str | None


class Job(BaseModel):
    id: int
    tipo: str
    entidade: str | None
    status: str  # PENDENTE | EXECUTANDO | SUCESSO | FALHA | CANCELADO
    tentativas: int
    criado_em: datetime
    concluido_em: datetime | None
    resultado: dict | None
    ultimo_erro: str | None
