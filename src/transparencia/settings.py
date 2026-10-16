"""Configuração central da aplicação.

Os valores vêm de variáveis de ambiente ou do arquivo `.env` na raiz do projeto
(veja `.env.example`). Usar pydantic-settings dá validação de tipos e um único
lugar para descobrir "de onde vem esta configuração?".
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = (
        "postgresql+psycopg://transparencia:transparencia@localhost:5432/transparencia"
    )

    # Arquivos de configuração versionados no Git
    criteria_catalog_path: Path = PROJECT_ROOT / "config" / "criteria_catalog.yaml"
    entities_path: Path = PROJECT_ROOT / "config" / "entities.yaml"
    evaluation_rules_path: Path = PROJECT_ROOT / "config" / "evaluation_rules.yaml"
    # Rótulos manuais (gabarito) — versionados: fazem parte da metodologia
    golden_path: Path = PROJECT_ROOT / "golden" / "golden_set.xlsx"

    # Onde ficam os snapshots coletados (HTML, PDFs, screenshots) — fora do Git
    data_dir: Path = PROJECT_ROOT / "data"

    # Identificação do coletor nos sites visitados. É o nome que o robots.txt enxerga,
    # e o link permite ao administrador do site saber quem está acessando e por quê.
    coletor_user_agent: str = (
        "MonitorTransparencia/0.1 (+https://github.com/santiquel/monitor-transparencia)"
    )

    # IA na zona cinzenta. "nenhum" = só regras; "ollama" = modelo local gratuito;
    # "anthropic" = Claude (pago, precisa de ANTHROPIC_API_KEY).
    llm_provedor: Literal["nenhum", "ollama", "anthropic"] = "nenhum"
    llm_model: str = "qwen2.5:7b"
    ollama_url: str = "http://localhost:11434"
    anthropic_api_key: str | None = None

    # Endereço da API, usado pelo dashboard
    api_url: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
