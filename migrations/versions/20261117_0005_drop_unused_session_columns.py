"""Drop session columns that no code path writes.

`saved_itinerary` was only filled by the retired email delivery, and `deleted_at` was never
set: retention deletes expired rows outright. Results live in Temporal workflow history.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261117_0005"
down_revision = "20261117_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("saved_itinerary")
        batch.drop_column("deleted_at")


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("saved_itinerary", sa.JSON(), nullable=True))
