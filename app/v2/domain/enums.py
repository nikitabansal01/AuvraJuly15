"""Finite state values shared by the v2 domain and persistence layers."""

from enum import Enum


class StringEnum(str, Enum):
    """Enum whose persisted and serialized representation is its value."""


class UserStatus(StringEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DELETION_PENDING = "deletion_pending"
    DELETED = "deleted"


class OnboardingStatus(StringEnum):
    ACTIVE = "active"
    CLAIMED = "claimed"
    EXPIRED = "expired"
    REVOKED = "revoked"


class PlanStatus(StringEnum):
    READY = "ready"
    ARCHIVED = "archived"


class PlanItemStatus(StringEnum):
    ACTIVE = "active"
    REPLACED = "replaced"
    RETIRED = "retired"


class MediaStatus(StringEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
    DELETED = "deleted"


class JobState(StringEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


class OutboxState(StringEnum):
    PENDING = "pending"
    RUNNING = "running"
    PUBLISHED = "published"
    FAILED = "failed"


class IdempotencyState(StringEnum):
    STARTED = "started"
    COMPLETED = "completed"
