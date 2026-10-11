"""Configuração central da aplicação.

Os valores vêm de variáveis de ambiente ou do arquivo `.env` na raiz do projeto
(veja `.env.example`). Usar pydantic-settings dá validação de tipos e um único
lugar para descobrir "de onde vem esta configuração?".
"""

from functools import lru_cache
from pathlib import Path

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

    # Onde ficam os snapshots coletados (HTML, PDFs, screenshots) — fora do Git
    data_dir: Path = PROJECT_ROOT / "data"

    # IA (usada a partir da Fase 3)
    anthropic_api_key: str | None = None
    llm_model: str = "claude-haiku-5-5"


@lru_cache
def get_settings() -> Settings:
    return Settings()
