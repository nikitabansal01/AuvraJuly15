"""Make the PostgreSQL outbox leaseable, bounded, and restart-safe.

Revision ID: 20260808_0010
Revises: 20260808_0009

The outbox remains the business source of truth.  A worker claims an event in a
short transaction, calls its event-bus adapter after that transaction commits,
then records the result in another guarded transaction.  Consequently a crash
after an adapter accepts an event may publish it again after lease recovery;
every consumer must deduplicate durably by the event UUID.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260808_0010"
down_revision = "20260808_0009"
branch_labels = depends_on = None


def upgrade() -> None:
    # The 0002 baseline supplied an already-prefixed source name while the
    # metadata naming convention added its own prefix.  A previous 0010
    # downgrade restores the canonical name, so accept exactly either known
    # predecessor and fail closed if neither constraint exists.
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'ops.outbox_events'::regclass
              AND conname = 'ck_outbox_events_ck_outbox_events_valid_state'
          ) THEN
            ALTER TABLE ops.outbox_events
              DROP CONSTRAINT ck_outbox_events_ck_outbox_events_valid_state;
          ELSIF EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'ops.outbox_events'::regclass
              AND conname = 'ck_outbox_events_valid_state'
          ) THEN
            ALTER TABLE ops.outbox_events
              DROP CONSTRAINT ck_outbox_events_valid_state;
          ELSE
            RAISE EXCEPTION 'outbox state constraint is missing before 0010 upgrade'
              USING ERRCODE = '42704';
          END IF;
        END $$;
        """
    )
    op.add_column(
        "outbox_events",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        schema="ops",
    )
    op.add_column(
        "outbox_events", sa.Column("lease_owner", sa.String(160)), schema="ops"
    )
    op.add_column(
        "outbox_events",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        schema="ops",
    )
    op.add_column(
        "outbox_events",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        schema="ops",
    )
    op.add_column("outbox_events", sa.Column("error_code", sa.String(64)), schema="ops")
    op.add_column(
        "outbox_events",
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        schema="ops",
    )
    op.create_check_constraint(
        "positive_max_attempts",
        "outbox_events",
        "max_attempts > 0",
        schema="ops",
    )
    op.execute(
        "ALTER TABLE ops.outbox_events ADD CONSTRAINT ck_outbox_events_valid_state "
        "CHECK (state IN ('pending','running','published','failed'))"
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM ops.outbox_events
            WHERE state = 'running'
               OR attempt_count <> 0
               OR max_attempts <> 5
               OR lease_owner IS NOT NULL
               OR lease_expires_at IS NOT NULL
               OR heartbeat_at IS NOT NULL
               OR error_code IS NOT NULL
               OR finished_at IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'cannot downgrade durable outbox schema after lease or retry facts exist'
              USING ERRCODE = '23514';
          END IF;
        END $$;
        """
    )
    op.drop_constraint("ck_outbox_events_valid_state", "outbox_events", schema="ops")
    op.drop_constraint(
        "ck_outbox_events_positive_max_attempts", "outbox_events", schema="ops"
    )
    op.drop_column("outbox_events", "finished_at", schema="ops")
    op.drop_column("outbox_events", "error_code", schema="ops")
    op.drop_column("outbox_events", "heartbeat_at", schema="ops")
    op.drop_column("outbox_events", "lease_expires_at", schema="ops")
    op.drop_column("outbox_events", "lease_owner", schema="ops")
    op.drop_column("outbox_events", "max_attempts", schema="ops")
    op.execute(
        "ALTER TABLE ops.outbox_events ADD CONSTRAINT ck_outbox_events_valid_state "
        "CHECK (state IN ('pending','published','failed'))"
    )
