"""State-transition policy for durable background jobs."""

from app.v2.domain.enums import JobState


class InvalidJobTransition(ValueError):
    """Raised when a caller attempts an impossible job transition."""


_ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.RUNNING, JobState.CANCELLED}),
    JobState.RUNNING: frozenset(
        {
            JobState.READY,
            JobState.RETRY_WAIT,
            JobState.FAILED,
            JobState.CANCELLED,
        }
    ),
    JobState.RETRY_WAIT: frozenset(
        {JobState.QUEUED, JobState.DEAD_LETTER, JobState.CANCELLED}
    ),
    JobState.FAILED: frozenset({JobState.QUEUED, JobState.DEAD_LETTER}),
    JobState.READY: frozenset(),
    JobState.CANCELLED: frozenset(),
    JobState.DEAD_LETTER: frozenset(),
}


def require_job_transition(current: JobState, target: JobState) -> None:
    """Validate a state change before persistence mutates the job row."""

    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidJobTransition(f"job cannot transition from {current} to {target}")
