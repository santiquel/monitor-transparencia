"""O resultado da avaliação de um critério: status + confiança + evidências.

Um veredito sem evidência não é aceito: todo status (inclusive NAO_ATENDE)
precisa dizer onde o sistema olhou. É isso que torna a avaliação auditável.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from transparencia.db.models import MetodoAvaliacao, StatusConformidade

VALOR_POR_STATUS = {
    StatusConformidade.ATENDE: Decimal("1"),
    StatusConformidade.ATENDE_PARCIALMENTE: Decimal("0.5"),
    StatusConformidade.NAO_ATENDE: Decimal("0"),
}


@dataclass
class Evidencia:
    tipo: str  # link, texto, tabela, formulario, documento, arquivo_dados, screenshot, elemento...
    descricao: str  # frase legível por humano
    url: str | None = None
    page_id: int | None = None
    titulo_pagina: str | None = None
    trecho: str | None = None  # citação literal do conteúdo
    seletor: str | None = None
    arquivo_path: str | None = None
    metadados: dict | None = None


@dataclass
class Veredito:
    status: StatusConformidade
    confianca: float
    justificativa: str
    evidencias: list[Evidencia] = field(default_factory=list)
    metodo: MetodoAvaliacao = MetodoAvaliacao.REGRA
    detalhes: dict = field(default_factory=dict)
    revisao_humana: bool = False
    llm_modelo: str | None = None
    llm_cache_key: str | None = None
    custo_llm_usd: float = 0.0

    @property
    def valor(self) -> Decimal | None:
        """1 / 0,5 / 0; None para NAO_APLICAVEL e NAO_AVALIADO (fora da conta)."""
        return VALOR_POR_STATUS.get(self.status)


def nao_avaliado(justificativa: str, evidencias=None, **detalhes) -> Veredito:
    """O sistema não conseguiu concluir. Não pesa na nota; aparece na cobertura."""
    return Veredito(
        StatusConformidade.NAO_AVALIADO,
        0.0,
        justificativa,
        evidencias or [],
        detalhes=detalhes,
        revisao_humana=True,
    )
