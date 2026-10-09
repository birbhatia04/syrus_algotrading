"""Add 021 runtime tables without modifying existing trading history."""
from alembic import op
from app.models import Instrument, MarketQuote, BrokerState, OrderRoute, StrategyDay

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None
TABLES = [Instrument.__table__, MarketQuote.__table__, BrokerState.__table__, OrderRoute.__table__, StrategyDay.__table__]

def upgrade():
    for table in TABLES:
        table.create(op.get_bind(), checkfirst=True)

def downgrade():
    for table in reversed(TABLES):
        table.drop(op.get_bind(), checkfirst=True)
