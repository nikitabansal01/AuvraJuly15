# AUVRA architecture and operations source of truth

**Release:** Initial v2 rebuild handover package  
**Evidence date:** 2026-08-01  
**Decision state:** Proposed; written owner approval is pending  
**Production state:** **Not production-ready and not approved for public release**

This directory is the version-controlled source for the AUVRA v2 architecture,
operations and handover package. The PDF is a generated snapshot, not a second
source of truth. Legacy documents outside this directory are historical evidence
unless this package explicitly incorporates them.

## Truth labels

- **AS-IS** means observed in a committed repository snapshot and backed by the
  evidence file.
- **TARGET PLANNED** means a governing v2 design, not proof of implementation.
- **TARGET PARTIAL** means code exists but its complete acceptance gate has not
  passed.
- **TARGET VERIFIED** requires dated test or operational evidence.
- **LEGACY ARCHIVE** means retained only for restricted migration, audit or
  characterization evidence.
- **REMOVE** means absent from final production after owner approval.

The frozen evidence baselines are backend commit
`c02892a8e1d5ec6bc76040e25df4e93d8fb60cfc` and mobile commit
`923e09c234c7b105d9f9ec15347ea555fa45a104`. Repository measurements come from
[`evidence/repository_snapshot.json`](evidence/repository_snapshot.json), whose
scanner reads committed Git objects and records no secret values or health-data
contents.

## Read in fifteen minutes

1. [`00-founder-summary.md`](00-founder-summary.md) - decisions, risks and the
   safe mental model.
2. [`07-traceability.md`](07-traceability.md) - retained journey to screen, API,
   table and metric.
3. [`diagrams/c4/context.mmd`](diagrams/c4/context.mmd) and
   [`diagrams/c4/container.mmd`](diagrams/c4/container.mmd) - system shape and
   trust direction.
4. [`runbooks/UNDER_PRESSURE.md`](runbooks/UNDER_PRESSURE.md) - first safe check
   during an incident.

## Package map

| Area | Source |
|---|---|
| Current evidence and legacy disposition | `01-as-is-and-legacy.md`, `evidence/`, table catalog |
| Target modular monolith and runtime | `02-target-architecture.md`, C4/deployment diagrams |
| Canonical data and governed metrics | `03-data-and-metrics.md`, data diagrams, table/metric catalogs |
| OpenAPI and mobile integration | `04-api-and-mobile.md`, API/feature catalogs |
| Trust, health-data lifecycle and threat boundaries | `05-security-privacy.md`, trust diagram |
| Delivery gates and acceptance | `06-delivery-and-acceptance.md` |
| Flow traceability | `07-traceability.md`, feature catalog |
| Safe change and handover | `08-handover-and-change-control.md`, ADRs, runbooks |
| Current physical schema inspection | `09-physical-schema-appendix.md`, generated from recovery migrations |
| Machine catalogs | `catalogs/*.json`, validated by `schemas/catalog.schema.json` |
| Decision records | `adr/ADR-001` through `ADR-015` |
| Diagrams | `diagrams/c4`, `diagrams/data`, `diagrams/sequence`, `diagrams/state` |
| PDF/HTML builder and validators | `build/` |

## Mandatory owner checkpoint

Before significant dependent implementation or public-release work, Nikita (or
the recorded product owner) must give written approval for:

1. feature dispositions in `catalogs/features.json`;
2. legacy-to-v2 table mapping and quarantine rules in `catalogs/tables.json`;
3. metric formulas and timezone policies in `catalogs/metrics.json`;
4. the target ERDs, API surface and the fifteen proposed ADRs; and
5. jurisdiction, consent language/versioning, retention periods, health-product
   classification and clinical escalation language.

Until then, catalog approval remains `PENDING_OWNER`; this document does not
claim medical, regulatory, privacy or production compliance.

## Build and validate

The build script uses ReportLab and the validation script uses JSON Schema,
`pypdf`, `pdfplumber` and Poppler. It creates the PDF, accessible HTML companion,
build manifest, checksum and rendered QA pages under `tmp/pdfs/`.

```sh
python docs/architecture/build/build_handbook.py
python docs/architecture/build/validate_release.py
```

The repository has a narrow documentation exception so `docs/architecture/build/`
is tracked while other generated build directories remain ignored.

## Contract-freeze hold

Source validation may continue while backend contracts change. Do not regenerate
or release the PDF/HTML/manifest snapshot until the backend contract owner
declares the API/OpenAPI surface frozen. A physical migration being present is
TARGET PARTIAL evidence only; it does not freeze an operationId or verify a
PostgreSQL 17 deployment.
