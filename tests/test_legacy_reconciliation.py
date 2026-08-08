from __future__ import annotations

import gzip
import json
import zipfile
from pathlib import Path

import pytest

from scripts.reconcile_legacy_backup import (
    RULES,
    ReconciliationError,
    build_reconciliation_report,
)


FIXTURE = (
    Path(__file__).parent / "fixtures" / "legacy_reconciliation" / "synthetic_rows.json"
)


def _write_synthetic_dump(path: Path, *, orphan: bool = False) -> int:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if orphan:
        fixture["daily_assignments"]["rows"][0][2] = "missing-schedule"
    row_total = sum(len(value["rows"]) for value in fixture.values())
    with gzip.open(path, "wt", encoding="utf-8") as output:
        output.write(
            "COPY public.alembic_version (version_num) FROM stdin;\n20260801_0002\n\\.\n"
        )
        for table, value in fixture.items():
            output.write(
                f"COPY public.{table} ({', '.join(value['columns'])}) FROM stdin;\n"
            )
            for row in value["rows"]:
                output.write("\t".join(row) + "\n")
            output.write("\\.\n")
    return row_total


def _write_storage(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as output:
        output.writestr("images/referenced.png", b"same")
        output.writestr("images/duplicate.png", b"same")
        output.writestr("images/orphaned.png", b"different")


def test_content_free_deterministic_one_to_one_ledger(tmp_path: Path) -> None:
    database = tmp_path / "synthetic.sql.gz"
    storage = tmp_path / "storage.zip"
    expected_total = _write_synthetic_dump(database)
    _write_storage(storage)

    first = build_reconciliation_report(
        database,
        storage,
        fingerprint_key="test-only-key",
        expected_total=expected_total,
    )
    second = build_reconciliation_report(
        database,
        storage,
        fingerprint_key="test-only-key",
        expected_total=expected_total,
    )
    rendered = json.dumps(first, sort_keys=True)

    assert first == second
    assert first["contains_source_payloads"] is False
    assert first["reconciliation"]["input_row_total"] == expected_total
    assert first["reconciliation"]["ledger_row_total"] == expected_total
    assert first["reconciliation"]["duplicate_ledger_identity_count"] == 0
    assert first["reconciliation"]["orphan_reference_count"] == 0
    assert len(first["records"]) == expected_total
    assert all(
        record["disposition"] in {"MIGRATE", "ARCHIVE", "QUARANTINE", "REMOVE"}
        for record in first["records"]
    )
    assert all(
        record["canonical_entities"] and record["row_fingerprint"]
        for record in first["records"]
    )
    assert first["storage"]["object_classification_counts"] == {
        "referenced": 1,
        "duplicate": 1,
        "orphaned": 1,
        "missing": 0,
    }
    for secret in (
        "REDACTED_EMAIL_TOKEN",
        "REDACTED_ANSWER_TOKEN",
        "REDACTED_REQUEST_TOKEN",
    ):
        assert secret not in rendered


def test_canonical_uuid_proposals_are_keyed(tmp_path: Path) -> None:
    database = tmp_path / "synthetic.sql.gz"
    storage = tmp_path / "storage.zip"
    expected_total = _write_synthetic_dump(database)
    _write_storage(storage)

    first = build_reconciliation_report(
        database,
        storage,
        fingerprint_key="first-private-key",
        expected_total=expected_total,
    )
    second = build_reconciliation_report(
        database,
        storage,
        fingerprint_key="second-private-key",
        expected_total=expected_total,
    )

    first_ids = [
        entity["canonical_entity_uuid"]
        for record in first["records"]
        for entity in record["canonical_entities"]
    ]
    second_ids = [
        entity["canonical_entity_uuid"]
        for record in second["records"]
        for entity in record["canonical_entities"]
    ]
    assert first_ids != second_ids


def test_orphaned_reference_is_quarantined_and_blocks_reconciliation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "orphan.sql.gz"
    storage = tmp_path / "storage.zip"
    expected_total = _write_synthetic_dump(database, orphan=True)
    _write_storage(storage)

    report = build_reconciliation_report(
        database,
        storage,
        fingerprint_key="test-only-key",
        expected_total=expected_total,
    )

    assert report["reconciliation"]["passed"] is False
    assert report["reconciliation"]["orphan_reference_count"] == 1
    assignment = next(
        record
        for record in report["records"]
        if record["legacy_table"] == "daily_assignments"
    )
    assert assignment["disposition"] == "QUARANTINE"
    assert assignment["reason_code"] == "ORPHAN_LEGACY_REFERENCE"
    assert report["reconciliation"]["orphan_summary_by_table_and_reason"] == {
        "daily_assignments": {"ORPHAN_LEGACY_REFERENCE": 1}
    }


def test_apply_is_guarded_to_an_isolated_pg17_target(tmp_path: Path) -> None:
    database = tmp_path / "synthetic.sql.gz"
    storage = tmp_path / "storage.zip"
    expected_total = _write_synthetic_dump(database)
    _write_storage(storage)

    with pytest.raises(ReconciliationError, match="attestation"):
        build_reconciliation_report(
            database,
            storage,
            fingerprint_key="test-only-key",
            expected_total=expected_total,
            mode="apply",
        )
    report = build_reconciliation_report(
        database,
        storage,
        fingerprint_key="test-only-key",
        expected_total=expected_total,
        mode="apply",
        target_attestation={
            "postgres_major": 17,
            "target_revision": "20260808_0011",
            "fresh_target": True,
        },
    )
    assert report["mode"] == "apply"
    assert report["reconciliation"]["apply_write_count"] == 0


def test_empty_storage_marks_a_referenced_object_missing(tmp_path: Path) -> None:
    database = tmp_path / "synthetic.sql.gz"
    storage = tmp_path / "empty-storage.zip"
    expected_total = _write_synthetic_dump(database)
    with zipfile.ZipFile(storage, "w"):
        pass

    report = build_reconciliation_report(
        database,
        storage,
        fingerprint_key="test-only-key",
        expected_total=expected_total,
    )

    assert report["storage"]["object_classification_counts"] == {
        "referenced": 0,
        "duplicate": 0,
        "orphaned": 0,
        "missing": 1,
    }
    assert len(report["storage"]["missing_objects"]) == 1
    assert "referenced.png" not in json.dumps(report, sort_keys=True)


def test_catalog_matches_the_runtime_table_and_id_contract() -> None:
    catalog_path = Path("docs/architecture/catalogs/legacy-disposition-rules.v1.json")
    schema_path = Path("docs/architecture/schemas/legacy-disposition-rules.schema.json")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert catalog["$schema"] == "../schemas/legacy-disposition-rules.schema.json"
    assert catalog["source_contract"]["application_row_total"] == 1_022
    assert {rule["legacy_table"] for rule in catalog["rules"]} == set(RULES)
    catalog_rules = {rule["legacy_table"]: rule for rule in catalog["rules"]}
    assert {
        table: rule["legacy_id_column"] for table, rule in catalog_rules.items()
    } == {table: rule.id_column for table, rule in RULES.items()}
    assert {table: rule["disposition"] for table, rule in catalog_rules.items()} == {
        table: rule.disposition for table, rule in RULES.items()
    }
    assert {table: rule["reason_code"] for table, rule in catalog_rules.items()} == {
        table: rule.reason_code for table, rule in RULES.items()
    }
    assert {
        table: tuple(rule["canonical_target_tables"])
        for table, rule in catalog_rules.items()
    } == {table: rule.target_entities for table, rule in RULES.items()}
    allowed = set(
        schema["properties"]["rules"]["items"]["properties"]["canonical_target_tables"][
            "items"
        ]["enum"]
    )
    assert {
        target for rule in RULES.values() for target in rule.target_entities
    } <= allowed
