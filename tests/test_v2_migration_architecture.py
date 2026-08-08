"""The active migration graph is a clean v2 shadow root, never legacy DDL."""
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_active_v2_chain_has_one_clean_root_and_no_legacy_baseline() -> None:
    versions = ROOT / "alembic" / "recovery_versions"
    files = {path.name: path.read_text() for path in versions.glob("*.py")}
    assert "20260723_0001_canonical_schema_baseline.py" not in files
    assert (
        "down_revision: Union[str, None] = None"
        in files["20260801_0002_auvra_v2_foundation.py"]
    )
    assert (
        "app.core.database import Base" not in (ROOT / "alembic" / "env.py").read_text()
    )


def test_legacy_snapshot_is_archived_with_its_checksum() -> None:
    evidence = ROOT / "alembic" / "legacy_evidence"
    snapshot = evidence / "20260723_0001_canonical_schema_baseline.py"
    assert snapshot.exists()
    assert snapshot.name in (evidence / "SHA256SUMS").read_text()


def test_completed_review_invariant_covers_all_mutation_paths() -> None:
    migration = (
        ROOT
        / "alembic"
        / "recovery_versions"
        / "20260808_0003_v2_engagement_governance.py"
    ).read_text()
    assert "assert_completed_review(p_review_id uuid)" in migration
    assert "ck_completed_review_coverage" in migration
    assert "ck_completed_review_items_coverage" in migration
    assert "ck_reviewed_plan_items_coverage" in migration
    assert "AFTER INSERT OR UPDATE OR DELETE ON app.daily_review_items" in migration
    assert "AFTER INSERT OR UPDATE OR DELETE ON app.action_plan_items" in migration
    assert "v_review_count <> v_plan_count" in migration
    assert "v_valid_count <> v_plan_count" in migration
    assert "review_item.answered_at IS NOT NULL" in migration
    assert "user_id = v_user_id" in migration
    assert "guard_completed_review_items" in migration
    assert "guard_reviewed_plan_items" in migration
    assert (
        "BEFORE INSERT OR UPDATE OF plan_id OR DELETE ON app.action_plan_items"
        in migration
    )
    assert "guard_completed_review_header" in migration
    assert "NEW.local_date IS DISTINCT FROM OLD.local_date" in migration
    assert "NEW.timezone IS DISTINCT FROM OLD.timezone" in migration
    assert (
        "UPDATE OF user_id, plan_id, local_date, timezone, status, completed_at"
        in migration
    )


def test_review_trigger_downgrade_is_symmetric() -> None:
    migration = (
        ROOT
        / "alembic"
        / "recovery_versions"
        / "20260808_0003_v2_engagement_governance.py"
    ).read_text()
    triggers = (
        "ck_completed_review_coverage",
        "ck_completed_review_items_coverage",
        "ck_reviewed_plan_items_coverage",
        "guard_completed_review_items",
        "guard_reviewed_plan_items",
        "guard_completed_review_header",
    )
    functions = (
        "assert_completed_review",
        "check_review_header",
        "check_review_item",
        "check_reviewed_plan_item",
        "guard_completed_review_item",
        "guard_reviewed_plan_item",
        "guard_completed_review_header",
    )
    for name in triggers:
        assert f"DROP TRIGGER IF EXISTS {name}" in migration
    for name in functions:
        assert f"DROP FUNCTION IF EXISTS app.{name}" in migration
