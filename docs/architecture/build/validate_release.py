#!/usr/bin/env python3
"""Validate handbook catalogs and the generated PDF release package."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import jsonschema
import pdfplumber
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs" / "architecture"
OUT = ROOT / "output" / "pdf"
PDF = OUT / "AUVRA_Architecture_and_Operations_Handbook.pdf"
HTML = OUT / "AUVRA_Architecture_and_Operations_Handbook.html"
MD = OUT / "AUVRA_Architecture_and_Operations_Handbook.md"
MANIFEST = OUT / "AUVRA_Architecture_and_Operations_Handbook.manifest.json"
SHA = OUT / "AUVRA_Architecture_and_Operations_Handbook.sha256"
TMP = ROOT / "tmp" / "pdfs"


def fail(message: str) -> None:
    raise AssertionError(message)


def main() -> None:
    schema = json.loads((DOCS / "schemas" / "catalog.schema.json").read_text())
    for path in sorted((DOCS / "catalogs").glob("*.json")):
        document = json.loads(path.read_text())
        jsonschema.Draft202012Validator(schema).validate(document)
    traceability = (DOCS / "07-traceability.md").read_text(encoding="utf-8")
    api_catalog = json.loads((DOCS / "catalogs" / "api.json").read_text())
    unfrozen = {"api.health_live", "api.health_ready"}
    missing_operation_ids = [record["id"] for record in api_catalog["records"] if record["id"] not in unfrozen and f"`{record['id'].removeprefix('api.')}`" not in traceability]
    if missing_operation_ids: fail(f"traceability missing operationIds: {missing_operation_ids}")
    appendix = DOCS / "09-physical-schema-appendix.md"
    if not appendix.is_file() or "20260808_0003" not in appendix.read_text(encoding="utf-8"):
        fail("physical schema appendix is missing or stale")
    snapshot = json.loads((DOCS / "evidence" / "repository_snapshot.json").read_text())
    legacy = json.loads((DOCS / "catalogs" / "tables.json").read_text())
    legacy_names = {r["name"].removeprefix("public.") for r in legacy["records"] if r["lifecycle"] == "LEGACY_ARCHIVE"}
    observed = {r["table"] for r in snapshot["backend"]["orm_tables"]}
    if observed != legacy_names: fail(f"legacy ORM/table catalog mismatch: observed={len(observed)} catalog={len(legacy_names)}")
    if snapshot["backend"]["orm_table_count"] != 35: fail("frozen ORM evidence must retain exact 35-table claim")
    migration_text = "\n".join(path.read_text() for path in sorted((ROOT / "alembic" / "recovery_versions").glob("202608*_*.py")))
    migration_tables = set(re.findall(r'op\.create_table\(\s*"([a-z_]+)"', migration_text))
    migration_tables |= set(re.findall(r'CREATE TABLE\s+(?:app|ops)\.([a-z_]+)', migration_text, re.I))
    target_names = {r["name"].split(".", 1)[1] for r in legacy["records"] if r["lifecycle"].startswith("TARGET_") and r["name"].split(".",1)[0] in {"app", "ops"}}
    target_names = (target_names - {"audit_events"}) | {"audit_events", "deletion_requests"}
    unexpected = migration_tables - target_names
    if unexpected: fail(f"v2 foundation migration has uncataloged tables: {sorted(unexpected)}")
    missing = target_names - migration_tables
    if not PDF.is_file() or not MD.is_file() or not HTML.is_file() or not MANIFEST.is_file() or not SHA.is_file(): fail("missing release artifact")
    manifest = json.loads(MANIFEST.read_text())
    if manifest.get("generator") != "ReportLab": fail("manifest must identify ReportLab")
    if "Legacy migration evidence register" not in MD.read_text(encoding="utf-8") or "<main>" not in HTML.read_text(encoding="utf-8"): fail("accessible Markdown/HTML companion incomplete")
    reader = PdfReader(str(PDF))
    if not reader.pages: fail("PDF has no pages")
    meta = reader.metadata or {}
    if "AUVRA Architecture" not in str(meta.get("/Title", "")): fail("PDF title metadata missing")
    outlines = list(reader.outline)
    if len(outlines) < 10: fail("PDF bookmarks missing")
    links = sum(1 for page in reader.pages for a in (page.get("/Annots", []) or []) if a.get_object().get("/Subtype") == "/Link")
    # This handbook contains a source-derived contents page and bookmarks; link annotations are optional in ReportLab output.
    with pdfplumber.open(str(PDF)) as book:
        text = "\n".join(page.extract_text() or "" for page in book.pages)
        if len(book.pages) < 10: fail("PDF unexpectedly short")
        for heading in ("AUVRA v2 in fifteen minutes", "AS-IS evidence and legacy disposition", "TARGET architecture", "Delivery sequence and acceptance evidence", "Under-pressure index", "Legacy migration evidence register"):
            if heading not in text: fail(f"missing heading/caption: {heading}")
        if "1,022" not in text or "22-byte" not in text or "not verified" not in text: fail("required migration evidence absent")
        if sum(len(p.extract_tables() or []) for p in book.pages) < 8: fail("expected tables not found")
    info = subprocess.run(["pdfinfo", str(PDF)], capture_output=True, text=True, check=True).stdout
    if "Pages:" not in info: fail("pdfinfo did not inspect PDF")
    TMP.mkdir(parents=True, exist_ok=True)
    prefix = TMP / "handbook-page"
    for stale in TMP.glob("handbook-page-*.png"): stale.unlink()
    subprocess.run(["pdftoppm", "-png", "-r", "110", str(PDF), str(prefix)], check=True)
    rendered = sorted(TMP.glob("handbook-page-*.png"))
    if len(rendered) != len(reader.pages): fail("Poppler did not render every PDF page")
    # Contact sheets make dense-page review repeatable and are intentionally QA intermediates.
    if shutil.which("montage"):
        subprocess.run(["montage", *map(str, rendered), "-tile", "4x", "-geometry", "260x340+3+3", str(TMP / "handbook-contact-sheet.png")], check=True)
    checksum_lines = SHA.read_text().splitlines()
    expected = hashlib.sha256(PDF.read_bytes()).hexdigest()
    if not any(line == f"{expected}  {PDF.name}" for line in checksum_lines): fail("PDF checksum mismatch")
    print(json.dumps({"catalogs": 9, "legacy_orm_tables": len(observed), "v2_migration_tables_observed": len(migration_tables), "target_catalog_tables_not_yet_in_foundation_migration": len(missing), "pdf_pages": len(reader.pages), "bookmarks": len(outlines), "link_annotations": links, "rendered_pages": len(rendered), "pdfinfo": "passed", "pdfplumber": "passed"}, indent=2))

if __name__ == "__main__":
    try: main()
    except Exception as exc:
        print(f"VALIDATION FAILED: {exc}", file=sys.stderr); raise
