"""Leitura e validação de `config/evaluation_rules.yaml` (o "como avaliar")."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from transparencia.db.models import StatusConformidade

# Status que um rótulo/rubrica pode usar (NAO_AVALIADO é só do sistema).
STATUS_RUBRICA = {
    StatusConformidade.ATENDE,
    StatusConformidade.ATENDE_PARCIALMENTE,
    StatusConformidade.NAO_ATENDE,
}


class RegraCriterio(BaseModel):
    rubrica: dict[StatusConformidade, str]
    onde_olhar: str

    @field_validator("rubrica")
    @classmethod
    def _rubrica_valida(cls, v: dict[StatusConformidade, str]) -> dict[StatusConformidade, str]:
        invalidos = set(v) - STATUS_RUBRICA
        if invalidos:
            raise ValueError(f"status não permitidos na rubrica: {sorted(invalidos)}")
        for obrigatorio in (StatusConformidade.ATENDE, StatusConformidade.NAO_ATENDE):
            if obrigatorio not in v:
                raise ValueError(f"rubrica sem {obrigatorio}")
        return v

    @property
    def admite_parcial(self) -> bool:
        return StatusConformidade.ATENDE_PARCIALMENTE in self.rubrica


class RegrasAvaliacao(BaseModel):
    versao: str
    criterios: dict[str, RegraCriterio] = Field(min_length=1)

    def codigos(self) -> list[str]:
        """Códigos em ordem natural (1.2 < 1.10 < 2.9 < 16.2)."""
        return sorted(self.criterios, key=lambda c: tuple(int(p) for p in c.split(".")))


def carregar_regras(caminho: Path) -> RegrasAvaliacao:
    with caminho.open(encoding="utf-8") as f:
        return RegrasAvaliacao.model_validate(yaml.safe_load(f))
