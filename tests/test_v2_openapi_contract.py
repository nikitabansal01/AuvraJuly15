"""The checked-in OpenAPI document is the deterministic FastAPI export."""
from __future__ import annotations

import hashlib

from scripts.export_v2_openapi import OUTPUT, encoded


def test_openapi_snapshot_is_current_and_complete() -> None:
    snapshot = OUTPUT.read_text(encoding="utf-8")
    assert snapshot == encoded()
    assert len(snapshot) > 20_000


def test_openapi_operations_have_unique_stable_ids_and_problem_responses() -> None:
    import json

    spec = json.loads(OUTPUT.read_text(encoding="utf-8"))
    operations = [
        operation
        for path in spec["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    ids = [operation["operationId"] for operation in operations]
    assert len(ids) == len(set(ids))
    for operation_id in (
        "putOnboardingAssessmentV2",
        "getMyCurrentPlanV2",
        "createConversationMessageV2",
        "putWeeklyCheckinResponseV2",
        "deleteMe",
    ):
        assert operation_id in ids
    assert all("422" in operation["responses"] for operation in operations)
    assert (
        hashlib.sha256(snapshot := OUTPUT.read_bytes()).hexdigest()
        == hashlib.sha256(snapshot).hexdigest()
    )
