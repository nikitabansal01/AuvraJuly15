#!/usr/bin/env python3
"""Create a content-free integrity manifest for the supplied legacy exports.

The verifier deliberately never prints COPY row contents.  It proves that the
reviewed cluster dump and storage archive are the same evidence used to design
the v2 migration before any restore or transformation is allowed.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import BinaryIO


COPY_HEADER = re.compile(r"^COPY public\.([a-z_][a-z0-9_]*) \(")

EXPECTED_APPLICATION_ROWS = {
    "daily_assignments": 145,
    "question_sessions": 4,
    "recommendation_advices": 498,
    "recommendation_completions": 1,
    "recommendation_records": 169,
    "recommendation_redistributions": 0,
    "recommendation_schedules": 141,
    "schedule_redistributions": 5,
    "session_processing_status": 8,
    "user_profiles": 25,
    "user_responses": 25,
    "user_schedules": 1,
}
EXPECTED_ALEMBIC_ROWS = 1
EXPECTED_APPLICATION_TOTAL = 1_022


class EvidenceMismatch(RuntimeError):
    """Raised when supplied evidence does not match the reviewed snapshot."""


def _sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return _sha256_stream(stream)


def _copy_counts(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    current_table: str | None = None
    current_count = 0

    try:
        stream = gzip.open(path, mode="rt", encoding="utf-8", errors="strict")
        with stream:
            for line_number, raw_line in enumerate(stream, start=1):
                line = raw_line.rstrip("\r\n")
                if current_table is None:
                    match = COPY_HEADER.match(line)
                    if match:
                        current_table = match.group(1)
                        if current_table in counts:
                            raise EvidenceMismatch(
                                f"duplicate COPY section for {current_table} at line {line_number}"
                            )
                        current_count = 0
                    continue

                if line == r"\.":
                    counts[current_table] = current_count
                    current_table = None
                    current_count = 0
                else:
                    current_count += 1
    except (OSError, UnicodeError) as exc:
        raise EvidenceMismatch("cluster export is not a valid UTF-8 gzip SQL dump") from exc

    if current_table is not None:
        raise EvidenceMismatch(f"unterminated COPY section for {current_table}")
    return counts


def inspect_database_export(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise EvidenceMismatch("database export does not exist")

    counts = _copy_counts(path)
    application_counts = {
        table: count for table, count in counts.items() if table != "alembic_version"
    }
    unexpected = sorted(set(application_counts) - set(EXPECTED_APPLICATION_ROWS))
    missing = sorted(set(EXPECTED_APPLICATION_ROWS) - set(application_counts))
    mismatched = {
        table: {"expected": expected, "actual": application_counts.get(table)}
        for table, expected in EXPECTED_APPLICATION_ROWS.items()
        if application_counts.get(table) != expected
    }
    alembic_rows = counts.get("alembic_version")
    application_total = sum(application_counts.values())

    if unexpected or missing or mismatched:
        raise EvidenceMismatch(
            "database COPY inventory differs from the reviewed table contract: "
            f"unexpected={unexpected}, missing={missing}, mismatched={mismatched}"
        )
    if application_total != EXPECTED_APPLICATION_TOTAL:
        raise EvidenceMismatch(
            f"expected {EXPECTED_APPLICATION_TOTAL} application rows, found {application_total}"
        )
    if alembic_rows != EXPECTED_ALEMBIC_ROWS:
        raise EvidenceMismatch(
            f"expected {EXPECTED_ALEMBIC_ROWS} alembic row, found {alembic_rows}"
        )

    return {
        "path_name": path.name,
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
        "format": "gzip_postgresql_cluster_sql",
        "application_row_total": application_total,
        "alembic_row_total": alembic_rows,
        "copy_row_counts": dict(sorted(application_counts.items())),
        "verified": True,
    }


def inspect_storage_export(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise EvidenceMismatch("storage export does not exist")

    try:
        with zipfile.ZipFile(path) as archive:
            corrupt_member = archive.testzip()
            if corrupt_member is not None:
                raise EvidenceMismatch("storage archive contains a corrupt member")
            entries = [entry for entry in archive.infolist() if not entry.is_dir()]
            total_uncompressed_bytes = sum(entry.file_size for entry in entries)
            member_names_sha256 = hashlib.sha256(
                "\n".join(sorted(entry.filename for entry in entries)).encode("utf-8")
            ).hexdigest()
    except zipfile.BadZipFile as exc:
        raise EvidenceMismatch("storage export is not a valid ZIP archive") from exc

    return {
        "path_name": path.name,
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
        "format": "zip",
        "object_count": len(entries),
        "uncompressed_bytes": total_uncompressed_bytes,
        "member_names_sha256": member_names_sha256,
        "classification": "empty_export" if not entries else "objects_present",
        "verified": True,
    }


def build_manifest(database_export: Path, storage_export: Path) -> dict[str, object]:
    database = inspect_database_export(database_export)
    storage = inspect_storage_export(storage_export)
    return {
        "manifest_version": 1,
        "contains_row_contents": False,
        "database_export": database,
        "storage_export": storage,
        "release_implications": {
            "requires_isolated_postgresql_17_restore": True,
            "may_restore_into_serving_database": False,
            "legacy_media_available": storage["object_count"] > 0,
            "missing_media_requires_regeneration_or_quarantine": storage["object_count"] == 0,
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-export", type=Path, required=True)
    parser.add_argument("--storage-export", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON destination. Parent directory must already exist.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        manifest = build_manifest(args.database_export, args.storage_export)
    except EvidenceMismatch as exc:
        print(f"Evidence verification failed: {exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.output:
        if not args.output.parent.is_dir():
            print("Evidence verification failed: output parent does not exist", file=sys.stderr)
            return 1
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
