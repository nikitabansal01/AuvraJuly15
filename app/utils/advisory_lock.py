"""Utilities for deterministic PostgreSQL advisory lock keys.

Why this exists:
- Python's built-in `hash()` is intentionally randomized per process (PYTHONHASHSEED).
- Using `hash()` for advisory lock keys makes the lock key differ across workers,
  defeating the purpose of serialization.

We instead derive a stable 31-bit positive integer key from SHA-256.
"""

from __future__ import annotations

import hashlib
from typing import Any


POSTGRES_INT32_MAX = 2_147_483_647


def advisory_lock_key(*parts: Any) -> int:
    """Return a deterministic positive int32-range key for pg_(try_)advisory_lock.

    PostgreSQL supports advisory locks using a bigint or two int keys.
    This project uses the single-argument form, so we generate a stable integer.
    """

    joined = ":".join("" if p is None else str(p) for p in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).digest()

    # Use 4 bytes to make an int, then constrain to signed int32 positive range.
    raw = int.from_bytes(digest[:4], byteorder="big", signed=False)
    return raw % POSTGRES_INT32_MAX
