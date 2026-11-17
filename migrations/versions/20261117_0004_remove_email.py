"""Remove recipient and delivery storage after retiring email-capable workers.

Stop the old web service, workers and retention job before applying this migration.
This removes stored recipient and delivery data; a downgrade cannot restore it.
"""

from alembic import op

revision = "20261117_0004"
down_revision = "20261009_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Remove retired delivery tables and recipient columns."""
    op.drop_table("email_webhooks")
    op.drop_table("email_deliveries")
    with op.batch_alter_table("sessions") as batch:
        batch.drop_index("ix_sessions_email_hash")
        batch.drop_column("email_hash")
        batch.drop_column("encrypted_email")


def downgrade() -> None:
    """Require a backup restore rather than implying recipient data is recoverable."""
    raise NotImplementedError("Restore a pre-migration backup to recover retired email storage")
