"""Esquemas (pydantic) dos arquivos de configuração YAML.

Validar o YAML na carga transforma erros de digitação em mensagens claras
("critério 4.1: classificacao inválida") em vez de falhas silenciosas na avaliação.
"""

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class Matriz(StrEnum):
    COMUM = "comum"
    ESPECIFICA_EXECUTIVO = "especifica_executivo"


class Classificacao(StrEnum):
    """Relevância legal do critério (inspirada no PNTP/Atricon)."""

    ESSENCIAL = "essencial"
    OBRIGATORIO = "obrigatorio"
    RECOMENDADO = "recomendado"


class Dimensao(StrEnum):
    """Qual aspecto da informação o critério verifica.

    O checklist repete os mesmos quesitos (pesquisa, exportação, atualização,
    histórico) em várias categorias. Marcar a dimensão permite analisar e
    ponderar esses quesitos de forma consistente.
    """

    DISPONIBILIDADE = "disponibilidade"
    ATUALIDADE = "atualidade"
    SERIE_HISTORICA = "serie_historica"
    GRAVACAO_RELATORIOS = "gravacao_relatorios"
    FILTRO_PESQUISA = "filtro_pesquisa"


class Poder(StrEnum):
    EXECUTIVO = "executivo"
    LEGISLATIVO = "legislativo"
    JUDICIARIO = "judiciario"
    AUTONOMO = "autonomo"  # MP, Defensoria, Tribunal de Contas


class Esfera(StrEnum):
    MUNICIPAL = "municipal"
    ESTADUAL = "estadual"
    FEDERAL = "federal"


# ---------------------------------------------------------------------------
# Catálogo de critérios
# ---------------------------------------------------------------------------


class Categoria(BaseModel):
    codigo: str
    nome: str
    bloco: str
    matriz: Matriz


class Criterio(BaseModel):
    codigo: str = Field(pattern=r"^\d+\.\d+$")
    categoria: str
    descricao: str
    fundamentacao: str | None = None
    fundamentacao_herdada: bool = False
    dimensao: Dimensao
    classificacao: Classificacao
    # Padrões "poder:esfera". Ex.: ["*"], ["executivo:*"], ["executivo:municipal"]
    aplica_a: list[str] = Field(default_factory=lambda: ["*"])
    # Critério cuja obrigação depende de um fato (ex.: só há "obras paralisadas"
    # se existirem obras paralisadas). Declaração expressa de inexistência atende.
    condicional: bool = False
    nota: str | None = None

    @field_validator("aplica_a")
    @classmethod
    def _valida_padroes(cls, v: list[str]) -> list[str]:
        for padrao in v:
            if padrao == "*":
                continue
            poder, _, esfera = padrao.partition(":")
            Poder(poder)  # levanta ValueError se inválido
            if esfera not in ("*", ""):
                Esfera(esfera)
        return v


class Catalogo(BaseModel):
    versao: str
    fonte: str
    pesos_classificacao: dict[Classificacao, float]
    categorias: list[Categoria]
    criterios: list[Criterio]
    observacoes_fonte: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistencia(self) -> "Catalogo":
        codigos_cat = {c.codigo for c in self.categorias}
        vistos: set[str] = set()
        for crit in self.criterios:
            if crit.categoria not in codigos_cat:
                raise ValueError(f"critério {crit.codigo}: categoria {crit.categoria} inexistente")
            if crit.codigo in vistos:
                raise ValueError(f"critério {crit.codigo} duplicado")
            vistos.add(crit.codigo)
        return self

    def content_hash(self) -> str:
        """Hash do conteúdo normativo. Muda se qualquer critério/peso mudar.

        Gravado no banco junto de cada avaliação: garante reprodutibilidade
        ("esta nota foi calculada com exatamente este catálogo").
        """
        payload = self.model_dump(mode="json", exclude={"versao", "observacoes_fonte"})
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(raw).hexdigest()

    def categoria(self, codigo: str) -> Categoria:
        return next(c for c in self.categorias if c.codigo == codigo)


# ---------------------------------------------------------------------------
# Cadastro de entidades
# ---------------------------------------------------------------------------


class Entidade(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]+$")
    nome: str
    municipio: str
    uf: str = Field(min_length=2, max_length=2)
    codigo_ibge: str | None = Field(default=None, pattern=r"^\d{7}$")
    poder: Poder
    esfera: Esfera
    url_site: HttpUrl
    # Opcional: se ausente, o coletor tenta descobrir o portal a partir do site.
    url_portal: HttpUrl | None = None
    # Exceção à política padrão de respeitar robots.txt (exige justificativa em `notas`).
    ignorar_robots: bool = False
    ativo: bool = True
    notas: str | None = None

    @property
    def tipo(self) -> str:
        return f"{self.poder.value}_{self.esfera.value}"


class CadastroEntidades(BaseModel):
    entidades: list[Entidade]

    @model_validator(mode="after")
    def _slugs_unicos(self) -> "CadastroEntidades":
        slugs = [e.slug for e in self.entidades]
        duplicados = {s for s in slugs if slugs.count(s) > 1}
        if duplicados:
            raise ValueError(f"slugs duplicados: {sorted(duplicados)}")
        return self
