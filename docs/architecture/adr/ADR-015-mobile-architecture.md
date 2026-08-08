# ADR-015: Generated-client, feature-aligned iOS/Android mobile architecture

- **Status:** Proposed - written owner approval pending
- **Date:** 2026-08-01
- **Owners:** Mobile engineering

## Context

The legacy app has large screens, raw fetch/token/URL duplication, AsyncStorage
workflow and sensitive data, duplicate DTOs, unused Expo Router and web scope.

## Decision

Support iOS/Android with React Navigation. Organize `core` plus backend-aligned
features. One OpenAPI-generated TypeScript client owns URL, token and transport.
TanStack Query owns server state; feature reducers/state machines own ephemeral
workflows. Firebase owns session truth. SecureStore holds necessary credentials,
never passwords/health drafts. One `PlanImage` uses `expo-image` and prefetched
hero images.

## Consequences and verification

Logout/account switch must purge query and UID-scoped local state. Raw fetch,
direct AsyncStorage health data, duplicate DTOs and web/router dependencies fail
CI. Component and Maestro tests cover both platforms, offline/process death,
account switching, image readiness and accessibility.

