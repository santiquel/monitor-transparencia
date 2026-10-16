"""Revisão humana (tabela revisoes) e resultado dos jobs.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16
"""

from pathlib import Path

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[1] / "sql" / "0003_revisoes.sql"


def upgrade() -> None:
    # Executa pelo cursor do psycopg SEM parâmetros: assim ele envia o SQL como está,
    # sem interpretar "%" (ex.: em comentários) como placeholder. O exec_driver_sql não
    # serve: ele repassa uma tupla vazia de parâmetros e o psycopg passa a interpretar "%".
    op.get_bind().connection.cursor().execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS resultado")
    op.execute("DROP TABLE IF EXISTS revisoes")
