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
    # exec_driver_sql envia o SQL como está ao driver (sem interpretar ":" ou "%").
    op.get_bind().exec_driver_sql(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    for view in ("v_criterio_historico", "v_score_historico", "v_ultima_avaliacao"):
        op.execute(f"DROP VIEW IF EXISTS {view}")
