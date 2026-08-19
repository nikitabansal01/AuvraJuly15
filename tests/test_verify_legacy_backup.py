from __future__ import annotations

import gzip
import zipfile

import pytest

from scripts.verify_legacy_backup import (
    EXPECTED_APPLICATION_ROWS,
    EvidenceMismatch,
    build_manifest,
    inspect_database_export,
)


def _write_database_export(path, *, count_delta: int = 0) -> None:
    sections = ["-- PostgreSQL database cluster dump\n"]
    sections.extend(
        [
            "COPY public.alembic_version (version_num) FROM stdin;\n",
            "20260801_0002\n",
            "\\.\n",
        ]
    )
    for table, count in EXPECTED_APPLICATION_ROWS.items():
        sections.append(f"COPY public.{table} (id) FROM stdin;\n")
        row_count = count + (count_delta if table == "question_sessions" else 0)
        sections.extend(f"{index}\n" for index in range(row_count))
        sections.append("\\.\n")
    with gzip.open(path, mode="wt", encoding="utf-8") as stream:
        stream.writelines(sections)


def test_build_manifest_never_contains_row_contents(tmp_path) -> None:
    database = tmp_path / "legacy.sql.gz"
    storage = tmp_path / "storage.zip"
    _write_database_export(database)
    with zipfile.ZipFile(storage, mode="w"):
        pass

    manifest = build_manifest(database, storage)

    assert manifest["contains_row_contents"] is False
    assert manifest["database_export"]["application_row_total"] == 1_022
    assert manifest["storage_export"]["classification"] == "empty_export"
    assert manifest["release_implications"]["legacy_media_available"] is False


def test_rejects_a_changed_row_inventory(tmp_path) -> None:
    database = tmp_path / "changed.sql.gz"
    _write_database_export(database, count_delta=1)

    with pytest.raises(EvidenceMismatch, match="differs from the reviewed table contract"):
        inspect_database_export(database)
