"""Esquema inicial: cadastro, catálogo, coleta, avaliação, pontuação e fila.

Revision ID: 0001
Revises:
Create Date: 2026-10-11
"""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[1] / "sql" / "0001_schema_inicial.sql"

TABELAS = [  # ordem inversa das dependências (filhos antes dos pais)
    "jobs",
    "scores",
    "evidences",
    "criterion_results",
    "evaluation_runs",
    "pages",
    "crawl_runs",
    "criteria",
    "catalog_versions",
    "entity_urls",
    "entities",
]


def upgrade() -> None:
    # exec_driver_sql envia o SQL como está ao driver (sem interpretar ":" ou "%").
    op.get_bind().exec_driver_sql(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    for tabela in TABELAS:
        op.execute(f"DROP TABLE IF EXISTS {tabela}")
