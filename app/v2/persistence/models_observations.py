"""The canonical user-observation table.

Preferences, body metrics, symptoms and period dates share one row grain,
so they share one table. This lives in its own module because it is a
distinct concept from the engagement ledgers, and because those ledgers
were already at the file-size limit.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.v2.persistence.base import APP_SCHEMA, V2Base


class UserObservation(V2Base):
    """One immutable, corrigible assertion by one user about one observable.

    Preferences, body metrics, symptoms and period dates all live here: they
    share a row grain, and only the read differs (latest value versus series).
    Values are typed columns rather than JSONB so they stay queryable and a
    malformed row is impossible.
    """

    __tablename__ = "user_observations"
    __table_args__ = (
        UniqueConstraint("user_id", "client_observation_id"),
        UniqueConstraint("supersedes_id"),
        CheckConstraint(
            "observation_type IN "
            "('symptom','body_metric','preference','cycle_event')",
            name="valid_type",
        ),
        CheckConstraint(
            "source IN ('user','onboarding_assessment','weekly_checkin',"
            "'conversation','import')",
            name="valid_source",
        ),
        CheckConstraint(
            "(source = 'user') = (source_id IS NULL)", name="source_id_presence"
        ),
        CheckConstraint(
            "num_nonnulls(value_numeric, value_codes, value_text) = 1",
            name="exactly_one_value",
        ),
        CheckConstraint(
            "(value_numeric IS NULL) = (value_unit IS NULL)",
            name="unit_iff_numeric",
        ),
        CheckConstraint(
            "value_codes IS NULL OR app.is_normalized_code_set(value_codes)",
            name="normalized_codes",
        ),
        CheckConstraint("btrim(observed_timezone) <> ''", name="timezone_nonempty"),
        CheckConstraint("btrim(code) <> ''", name="code_nonempty"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    observation_type: Mapped[str] = mapped_column(String(24), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    catalog_version: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="observations.v1"
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: The immutable user-local day the assertion is about, with the timezone
    #: that produced it, so daily aggregates never re-derive a past decision.
    observed_local_date: Mapped[date] = mapped_column(Date, nullable=False)
    observed_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    value_unit: Mapped[str | None] = mapped_column(String(16))
    value_codes: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    value_text: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="user"
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    #: A correction cites the assertion it replaces rather than rewriting it.
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app.user_observations.id", ondelete="RESTRICT")
    )
    client_observation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    #: Distinct from observed_at so a plan's cycle snapshot can be replayed
    #: against exactly the observations that existed when it was published.
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    note: Mapped[str | None] = mapped_column(Text)
