"""Views de histórico (última avaliação, série de scores, linha do tempo por critério).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-11
"""

from pathlib import Path

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[1] / "sql" / "0002_views_historico.sql"


def upgrade() -> None:
    # Executa pelo cursor do psycopg SEM parâmetros: assim ele envia o SQL como está,
    # sem interpretar "%" (ex.: em comentários) como placeholder. O exec_driver_sql não
    # serve: ele repassa uma tupla vazia de parâmetros e o psycopg passa a interpretar "%".
    op.get_bind().connection.cursor().execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    for view in ("v_criterio_historico", "v_score_historico", "v_ultima_avaliacao"):
        op.execute(f"DROP VIEW IF EXISTS {view}")
