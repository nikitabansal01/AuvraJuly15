# ADR-005: Immutable local-day decisions and engagement ledgers

- **Status:** Proposed - schedule policy approval pending
- **Date:** 2026-08-01
- **Owners:** Progress, streaks, rewards and refreshes

## Context

Legacy state stores mutable streak counters, freeze counts/date arrays and daily
refresh counters, making repair and timezone behavior ambiguous.

## Decision

Store event instants in UTC plus the immutable IANA timezone and local date used
for each daily decision. `streak_days` records one finalized qualifying, frozen
or missed closed day with evidence. `reward_ledger` stores grants, redemptions
and expirations. Refresh usage is the count of accepted `plan_refreshes`.

## Consequences and verification

Current/longest streak and balances are reproducible and repairable from facts.
The current day may be provisional but not finalized early. Property tests cover
midnight, DST gaps/folds, travel/timezone changes, delayed events, freeze races
and ledger reconciliation.

