# ADR-008: Versioned AI gateway, evidence and fail-closed safety

- **Status:** Proposed - provider/model and clinical approval pending
- **Date:** 2026-08-01
- **Owners:** Evidence, AI safety and evaluation

## Context

Provider calls and prompts are scattered, model fallbacks vary, and health-facing
output requires traceable evidence and safety review.

## Decision

Provider SDKs exist only behind gateway adapters. Prompts are source-controlled by
task, version, output schema, model policy and safety policy. Generation uses
structured output, authoritative evidence, citation validation and deterministic
plus evaluated clinical-safety gates. Unsafe, unverifiable or structurally invalid
content fails before publication. Medical-persona/diagnostic claims are prohibited.
Invocation telemetry stores metadata/cost/latency/status, not long-term raw health
prompts.

## Consequences and verification

Primary/fallback selection is replaceable and benchmarked in staging after key
rotation/privacy review. Golden, adversarial, red-flag, provider-timeout and cost
tests gate every prompt/model version.

