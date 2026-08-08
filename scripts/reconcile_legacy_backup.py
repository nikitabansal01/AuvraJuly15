#!/usr/bin/env python3
"""Create a deterministic, content-free disposition ledger for a legacy dump.

This is intentionally an *offline* migration gate.  It reads a PostgreSQL COPY
dump and a storage ZIP, but never opens a database connection and never emits
source values.  In particular, fingerprints are HMACs, not hashes that make
small health-answer domains easy to enumerate.

``dry-run`` and ``apply`` produce the same ledger.  Apply is deliberately a
no-write authorization step until an isolated PostgreSQL 17 rehearsal supplies
its target attestation; it cannot be pointed at Supabase, Render, or any other
database service.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import hmac
import json
import os
import re
import sys
import uuid
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from urllib.parse import unquote, urlparse

try:  # Supports both ``python -m scripts...`` and direct operator invocation.
    from scripts.verify_legacy_backup import (
        EvidenceMismatch,
        inspect_database_export,
        inspect_storage_export,
    )
except ModuleNotFoundError:  # pragma: no cover - exercised by the CLI path.
    from verify_legacy_backup import (  # type: ignore[no-redef]
        EvidenceMismatch,
        inspect_database_export,
        inspect_storage_export,
    )


RECONCILIATION_VERSION = "1.0.0"
FINGERPRINT_ALGORITHM = "hmac-sha256-v1"
TARGET_SCHEMA_REVISION = "20260808_0011"
COPY_HEADER = re.compile(r"^COPY public\.([a-z_][a-z0-9_]*) \((.*)\) FROM stdin;$")
MEDIA_TOKEN = re.compile(
    r"(?:https?://[^\s\"']+|(?:[A-Za-z0-9_.@%+\-/]+\.(?:png|jpe?g|webp|gif|svg)))",
    re.IGNORECASE,
)


class ReconciliationError(RuntimeError):
    """Raised when the evidence cannot be classified without guessing."""


@dataclass(frozen=True)
class DispositionRule:
    id_column: str
    disposition: str
    reason_code: str
    target_entities: tuple[str, ...]


# Names are the only canonical names introduced by Alembic revisions 0002/0003.
# The deterministic UUID is a proposed identity only; it becomes a serving-row
# UUID only if its disposition is MIGRATE and an isolated apply is approved.
RULES: Mapping[str, DispositionRule] = {
    "daily_assignments": DispositionRule(
        "id", "ARCHIVE", "LEGACY_DERIVED_ASSIGNMENT", ("app.daily_review_items",)
    ),
    "question_sessions": DispositionRule(
        "session_id",
        "QUARANTINE",
        "CONSENT_NOT_EVIDENCED",
        ("app.onboarding_sessions",),
    ),
    "recommendation_advices": DispositionRule(
        "id", "ARCHIVE", "LEGACY_DERIVED_ADVICE", ("app.action_plan_item_variants",)
    ),
    "recommendation_completions": DispositionRule(
        "id", "QUARANTINE", "CANONICAL_ITEM_NOT_PROVEN", ("app.action_item_events",)
    ),
    "recommendation_records": DispositionRule(
        "id", "ARCHIVE", "LEGACY_GENERATED_PLAN_NOT_CANONICAL", ("app.action_plans",)
    ),
    "recommendation_redistributions": DispositionRule(
        "id", "ARCHIVE", "LEGACY_SCHEDULE_STATE", ("app.plan_refreshes",)
    ),
    "recommendation_schedules": DispositionRule(
        "id", "ARCHIVE", "LEGACY_SCHEDULE_STATE", ("app.action_plans",)
    ),
    "schedule_redistributions": DispositionRule(
        "id", "ARCHIVE", "LEGACY_SCHEDULE_STATE", ("app.plan_refreshes",)
    ),
    "session_processing_status": DispositionRule(
        "session_id",
        "QUARANTINE",
        "LEGACY_PROCESSING_PAYLOAD",
        ("ops.generation_jobs",),
    ),
    "user_profiles": DispositionRule(
        "uid", "QUARANTINE", "CONSENT_NOT_EVIDENCED", ("app.users", "app.user_profiles")
    ),
    "user_responses": DispositionRule(
        "id", "QUARANTINE", "CONSENT_NOT_EVIDENCED", ("app.onboarding_assessments",)
    ),
    "user_schedules": DispositionRule(
        "id", "ARCHIVE", "LEGACY_DERIVED_SCHEDULE", ("app.daily_reviews",)
    ),
}

# These are source-integrity checks only.  They do not assert that a target row
# should be created, and no referenced source value is put into the report.
REFERENCES: Mapping[str, tuple[tuple[str, str, str], ...]] = {
    "daily_assignments": (
        ("uid", "user_profiles", "uid"),
        ("schedule_id", "recommendation_schedules", "id"),
        ("recommendation_id", "recommendation_records", "id"),
    ),
    "recommendation_advices": (
        ("recommendation_id", "recommendation_records", "id"),
        ("uid", "user_profiles", "uid"),
        ("session_id", "question_sessions", "session_id"),
    ),
    "recommendation_completions": (
        ("uid", "user_profiles", "uid"),
        ("recommendation_id", "recommendation_records", "id"),
    ),
    "recommendation_records": (
        ("uid", "user_profiles", "uid"),
        ("session_id", "question_sessions", "session_id"),
    ),
    "recommendation_redistributions": (
        ("uid", "user_profiles", "uid"),
        ("recommendation_id", "recommendation_records", "id"),
    ),
    "recommendation_schedules": (
        ("uid", "user_profiles", "uid"),
        ("recommendation_id", "recommendation_records", "id"),
    ),
    "schedule_redistributions": (("schedule_id", "recommendation_schedules", "id"),),
    "session_processing_status": (("session_id", "question_sessions", "session_id"),),
    "user_responses": (("uid", "user_profiles", "uid"),),
    "user_schedules": (("uid", "user_profiles", "uid"),),
}


def _hmac(key: bytes, value: str) -> str:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def _uuid_for(
    key: bytes,
    target_table: str,
    legacy_table: str,
    legacy_id: str,
) -> str:
    """Derive a stable, keyed UUID without publishing a reversible ID mapping."""

    digest = bytearray(
        hmac.new(
            key,
            f"canonical-id\x00{target_table}\x00{legacy_table}\x00{legacy_id}".encode(
                "utf-8"
            ),
            hashlib.sha256,
        ).digest()[:16]
    )
    digest[6] = (digest[6] & 0x0F) | 0x50
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def _unescape_copy(value: str) -> str | None:
    if value == r"\N":
        return None
    result: list[str] = []
    index = 0
    simple = {
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "v": "\v",
        "\\": "\\",
    }
    while index < len(value):
        char = value[index]
        if char != "\\" or index + 1 == len(value):
            result.append(char)
            index += 1
            continue
        escaped = value[index + 1]
        if escaped in simple:
            result.append(simple[escaped])
            index += 2
        elif (
            escaped.isdigit()
            and index + 3 < len(value)
            and value[index + 1 : index + 4].isdigit()
        ):
            result.append(chr(int(value[index + 1 : index + 4], 8)))
            index += 4
        else:
            result.append(escaped)
            index += 2
    return "".join(result)


def _parse_copy_dump(
    path: Path,
) -> dict[str, tuple[tuple[str, ...], list[tuple[str, dict[str, str | None]]]]]:
    """Parse only structured COPY fields in memory; never return or print rows."""
    parsed: dict[
        str, tuple[tuple[str, ...], list[tuple[str, dict[str, str | None]]]]
    ] = {}
    current_name: str | None = None
    current_columns: tuple[str, ...] = ()
    current_rows: list[tuple[str, dict[str, str | None]]] = []
    try:
        stream = gzip.open(path, mode="rt", encoding="utf-8", errors="strict")
        with stream:
            for line_number, raw_line in enumerate(stream, start=1):
                line = raw_line.rstrip("\r\n")
                if current_name is None:
                    match = COPY_HEADER.match(line)
                    if not match:
                        continue
                    current_name = match.group(1)
                    if current_name in parsed:
                        raise ReconciliationError("duplicate COPY section")
                    current_columns = tuple(
                        column.strip() for column in match.group(2).split(",")
                    )
                    if len(current_columns) != len(set(current_columns)) or not all(
                        current_columns
                    ):
                        raise ReconciliationError("ambiguous COPY column definition")
                    current_rows = []
                    continue
                if line == r"\.":
                    parsed[current_name] = (current_columns, current_rows)
                    current_name = None
                    continue
                values = line.split("\t")
                if len(values) != len(current_columns):
                    raise ReconciliationError(
                        f"COPY field count mismatch at line {line_number}"
                    )
                current_rows.append(
                    (line, dict(zip(current_columns, map(_unescape_copy, values))))
                )
    except (OSError, UnicodeError) as exc:
        raise ReconciliationError(
            "database export is not a readable UTF-8 gzip COPY dump"
        ) from exc
    if current_name is not None:
        raise ReconciliationError("unterminated COPY section")
    return parsed


def _walk_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)


def _source_media_references(rows: Iterable[dict[str, str | None]]) -> set[str]:
    """Extract media-like tokens without retaining any source payload in output."""
    references: set[str] = set()
    for row in rows:
        for raw_value in row.values():
            if not raw_value:
                continue
            candidate: object = raw_value
            try:
                candidate = json.loads(raw_value)
            except (TypeError, json.JSONDecodeError):
                pass
            for text in _walk_strings(candidate):
                for token in MEDIA_TOKEN.findall(text):
                    parsed = urlparse(token)
                    references.add(
                        unquote((parsed.path if parsed.scheme else token)).lstrip("/")
                    )
    return references


def _classify_storage(
    path: Path, source_references: set[str], key: bytes
) -> dict[str, object]:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = sorted(
                (entry for entry in archive.infolist() if not entry.is_dir()),
                key=lambda entry: entry.filename,
            )
            names = {entry.filename for entry in entries}
            normalized_reference_matches: set[str] = set()
            for reference in source_references:
                matches = [
                    name
                    for name in names
                    if name == reference
                    or reference.endswith("/" + name)
                    or name.endswith("/" + reference)
                ]
                normalized_reference_matches.update(matches)
            content_hashes = {
                entry.filename: hashlib.sha256(archive.read(entry)).hexdigest()
                for entry in entries
            }
            grouped_names: dict[str, list[str]] = defaultdict(list)
            for name, content_hash in content_hashes.items():
                grouped_names[content_hash].append(name)
            # A referenced copy is canonical when identical objects exist;
            # this makes referenced/duplicate exclusive and deterministic.
            canonical_by_content = {
                content_hash: sorted(
                    names,
                    key=lambda name: (name not in normalized_reference_matches, name),
                )[0]
                for content_hash, names in grouped_names.items()
            }
            objects: list[dict[str, object]] = []
            for entry in entries:
                content_hash = content_hashes[entry.filename]
                if canonical_by_content[content_hash] != entry.filename:
                    classification = "duplicate"
                elif entry.filename in normalized_reference_matches:
                    classification = "referenced"
                else:
                    classification = "orphaned"
                objects.append(
                    {
                        "object_key_fingerprint": _hmac(
                            key, "storage-key\x00" + entry.filename
                        ),
                        "content_sha256": content_hash,
                        "bytes": entry.file_size,
                        "classification": classification,
                    }
                )
            missing = sorted(
                reference
                for reference in source_references
                if not any(
                    reference == name
                    or reference.endswith("/" + name)
                    or name.endswith("/" + reference)
                    for name in names
                )
            )
    except zipfile.BadZipFile as exc:
        raise ReconciliationError("storage export is not a valid ZIP archive") from exc

    missing_objects = [
        {
            "object_key_fingerprint": _hmac(key, "storage-key\x00" + reference),
            "classification": "missing",
        }
        for reference in missing
    ]
    all_classifications = [item["classification"] for item in objects] + [
        "missing"
    ] * len(missing_objects)
    classification_counts = Counter(all_classifications)
    return {
        "object_count": len(objects),
        "object_classification_counts": {
            name: classification_counts[name]
            for name in ("referenced", "duplicate", "orphaned", "missing")
        },
        "objects": objects,
        "missing_objects": missing_objects,
    }


def _validate_inventory(parsed: Mapping[str, object], expected_total: int) -> None:
    supplied = set(parsed) - {"alembic_version"}
    expected = set(RULES)
    if supplied != expected:
        raise ReconciliationError(
            "COPY table inventory does not match the versioned disposition catalog"
        )
    total = sum(len(parsed[table][1]) for table in RULES)  # type: ignore[index]
    if total != expected_total:
        raise ReconciliationError(
            "application row total differs from the required reconciliation total"
        )


def build_reconciliation_report(
    database_export: Path,
    storage_export: Path,
    *,
    fingerprint_key: str,
    expected_total: int = 1_022,
    mode: str = "dry-run",
    target_attestation: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the deterministic ledger.  The returned object contains no COPY values."""
    if mode not in {"dry-run", "apply"}:
        raise ReconciliationError("mode must be dry-run or apply")
    if not fingerprint_key:
        raise ReconciliationError("a non-empty private fingerprint key is required")
    if mode == "apply":
        if target_attestation is None:
            raise ReconciliationError(
                "apply requires an isolated PostgreSQL 17 target attestation"
            )
        if (
            target_attestation.get("postgres_major") != 17
            or target_attestation.get("target_revision") != TARGET_SCHEMA_REVISION
            or target_attestation.get("fresh_target") is not True
        ):
            raise ReconciliationError(
                "apply attestation does not prove a fresh PostgreSQL 17 target "
                f"at revision {TARGET_SCHEMA_REVISION}"
            )

    key = fingerprint_key.encode("utf-8")
    parsed = _parse_copy_dump(database_export)
    _validate_inventory(parsed, expected_total)
    all_rows = [row for table in RULES for _, row in parsed[table][1]]  # type: ignore[index]
    source_references = _source_media_references(all_rows)

    id_sets: dict[tuple[str, str], set[str]] = {}
    for table, rule in RULES.items():
        columns, rows = parsed[table]
        if rule.id_column not in columns:
            raise ReconciliationError("required legacy identifier column is absent")
        ids = [row[rule.id_column] for _, row in rows]
        if any(not identifier for identifier in ids) or len(ids) != len(set(ids)):
            raise ReconciliationError(
                "legacy table has absent or duplicate primary identifiers"
            )
        id_sets[(table, rule.id_column)] = set(ids)  # type: ignore[arg-type]

    records: list[dict[str, object]] = []
    orphan_reference_count = 0
    orphan_summary: Counter[tuple[str, str]] = Counter()
    for table, rule in RULES.items():
        _, rows = parsed[table]
        for raw_line, row in rows:
            legacy_id = row[rule.id_column]
            assert legacy_id is not None
            missing_reference_edges = [
                f"{table}.{source_column}->{referenced_table}.{referenced_column}"
                for source_column, referenced_table, referenced_column in REFERENCES.get(
                    table, ()
                )
                if row.get(source_column) is not None
                and row[source_column]
                not in id_sets[(referenced_table, referenced_column)]
            ]
            orphaned = bool(missing_reference_edges)
            disposition = "QUARANTINE" if orphaned else rule.disposition
            reason_code = "ORPHAN_LEGACY_REFERENCE" if orphaned else rule.reason_code
            orphan_reference_count += int(orphaned)
            if orphaned:
                orphan_summary[(table, reason_code)] += 1
            entities = [
                {
                    "target_table": target,
                    "canonical_entity_uuid": _uuid_for(key, target, table, legacy_id),
                }
                for target in rule.target_entities
            ]
            records.append(
                {
                    "legacy_table": table,
                    "legacy_id": legacy_id,
                    "canonical_entities": entities,
                    "disposition": disposition,
                    "reason_code": reason_code,
                    "row_fingerprint": _hmac(key, f"row\x00{table}\x00{raw_line}"),
                }
            )

    if (
        len(records) != expected_total
        or len({(record["legacy_table"], record["legacy_id"]) for record in records})
        != expected_total
    ):
        raise ReconciliationError(
            "row ledger is not one-to-one with the required input inventory"
        )
    storage = _classify_storage(storage_export, source_references, key)
    dispositions = Counter(record["disposition"] for record in records)
    orphan_summary_rendered: dict[str, dict[str, int]] = {}
    for (table, reason_code), count in sorted(orphan_summary.items()):
        orphan_summary_rendered.setdefault(table, {})[reason_code] = count
    report = {
        "reconciliation_version": RECONCILIATION_VERSION,
        "mode": mode,
        "contains_source_payloads": False,
        "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
        "input": {
            "database_export": {
                "path_name": database_export.name,
                "sha256": _sha256_file(database_export),
                "application_row_total": expected_total,
            },
            "storage_export": {
                "path_name": storage_export.name,
                "sha256": _sha256_file(storage_export),
            },
        },
        "records": records,
        "storage": storage,
        "reconciliation": {
            "input_row_total": expected_total,
            "ledger_row_total": len(records),
            "duplicate_ledger_identity_count": 0,
            "orphan_reference_count": orphan_reference_count,
            "orphan_summary_by_table_and_reason": orphan_summary_rendered,
            "disposition_counts": dict(sorted(dispositions.items())),
            "deterministic_order": "legacy_table_catalog_then_legacy_id_source_order",
            "apply_write_count": int(dispositions.get("MIGRATE", 0)),
            "passed": orphan_reference_count == 0,
        },
    }
    return report


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-export", required=True, type=Path)
    parser.add_argument("--storage-export", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mode", choices=("dry-run", "apply"), default="dry-run")
    parser.add_argument(
        "--fingerprint-key-env", default="LEGACY_RECONCILIATION_FINGERPRINT_KEY"
    )
    parser.add_argument("--target-attestation", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.output.parent.is_dir():
        print("Reconciliation failed: output parent does not exist", file=sys.stderr)
        return 1
    try:
        # This supplies the reviewed *per-table* 1,022-row contract before the
        # row parser runs.  It deliberately has no row-content output.
        inspect_database_export(args.database_export)
        inspect_storage_export(args.storage_export)
        attestation = None
        if args.target_attestation:
            attestation = json.loads(
                args.target_attestation.read_text(encoding="utf-8")
            )
        report = build_reconciliation_report(
            args.database_export,
            args.storage_export,
            fingerprint_key=os.environ.get(args.fingerprint_key_env, ""),
            mode=args.mode,
            target_attestation=attestation,
        )
    except (
        EvidenceMismatch,
        ReconciliationError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"Reconciliation failed: {exc}", file=sys.stderr)
        return 1
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("Reconciliation completed: content-free ledger written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
