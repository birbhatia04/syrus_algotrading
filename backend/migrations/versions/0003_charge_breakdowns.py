"""Persist immutable execution and position charge breakdowns."""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("executions", sa.Column("charge_breakdown", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("positions", sa.Column("charge_breakdown", sa.Text(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("positions", "charge_breakdown")
    op.drop_column("executions", "charge_breakdown")
