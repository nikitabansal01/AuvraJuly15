# Feature-to-screen-to-API-to-table-to-metric traceability

This is the human-readable alignment matrix. The complete machine form is
`catalogs/features.json`; approval remains pending.

| Retained flow | Screen(s) | operationId(s) | Canonical table(s) | Metric ID |
|---|---|---|---|---|
| Identity/session | Splash, Login, SignupLoading | `get_me_profile`, `patch_me_profile`, `delete_me` | `app.users`, `app.user_profiles`, `app.audit_events`, `ops.deletion_requests` | `metric.api_availability` |
| Guest onboarding and claim | Onboarding, Question, Result, Researching | `create_onboarding_session`, `put_onboarding_assessment`, `claim_onboarding_session` | `app.onboarding_sessions`, `app.onboarding_assessments`, `app.consent_records` | `metric.plan_generation_success` (downstream) |
| Generate and view daily plan | Researching, Home, ActionDetail | `create_plan_generation`, `get_job`, `get_today_plan`, `get_plan` | `ops.generation_jobs`, `ops.outbox_events`, `app.action_plans`, `app.action_plan_items`, `app.action_plan_item_variants`, `app.media_assets` | `metric.plan_generation_success`, `metric.ready_plan_completeness`, `metric.ai_cost_per_ready_plan` |
| Completion, skip and feedback | Home, ActionDetail, ActionCompleted | `record_action_event`, `record_action_feedback` | `app.action_item_events`, `ops.idempotency_keys` | `metric.action_completion`, `metric.daily_adherence` |
| Daily Review | Home, DailyReview modal | `put_daily_review` | `app.daily_reviews`, `app.daily_review_items`, `ops.idempotency_keys` | `metric.daily_adherence`, `metric.daily_review_persistence` |
| Refresh, replacement, streak and reward | Home, Personalize, Progress | `replacePlanWithSelectedVariantV2`, `getMyProgressSummaryV2` | `app.plan_refreshes`, `app.action_plans`, `app.action_plan_items`, `app.streak_days`, `app.reward_ledger` | `metric.refreshes_used`, `metric.current_streak` |
| General conversation | Chatbot, ChatHistory | `list_conversations`, `create_conversation`, `get_conversation`, `create_conversation_message` | `app.conversations`, `app.conversation_messages`, `app.conversation_summaries`, `ops.generation_jobs` | `metric.api_availability` |
| Weekly check-in | Chatbot, Home | `get_weekly_checkin_due`, `create_weekly_checkin`, `put_weekly_checkin_response` | `app.weekly_checkins`, `app.weekly_checkin_questions`, `app.weekly_checkin_responses` | `metric.weekly_checkin_completion` |
| Care-plan check-in | Chatbot | `list_conversations`, `create_conversation`, `create_conversation_message` | `app.conversations`, `app.conversation_messages`, `app.conversation_summaries` | `metric.api_availability` |
| Symptom check-in | Chatbot, Home | `create_symptom_observation`, `create_conversation_message` | `app.symptom_observations`, `app.conversations`, `app.conversation_messages` | `metric.api_availability` |
| Profile, export and erasure | Profile | `get_me_profile`, `patch_me_profile`, `create_my_export`, `delete_me` | `app.users`, `app.user_profiles`, `app.audit_events`, `ops.generation_jobs`, `ops.deletion_requests` | `metric.api_availability` |
| Insights (owner approval required) | Insights | No frozen operationId | `app.symptom_observations`, `app.action_item_events` | No approved metric |
| Mood tracking | Inactive component | None - LEGACY ARCHIVE | None in serving schema | None |
| Paywall/community/test screens | Mock/test/unapproved surfaces | None - REMOVE | None | None |
| Web client | Web-only branches/config | None - REMOVE | None | None |

## Trace rule

A retained screen must point to exactly one generated client operation. That
operation points to one owning application command/query. Writes end in one
canonical table family plus idempotency/outbox infrastructure. Displayed product
numbers point to one metric record. If two active paths claim the same concept,
the change is blocked until one becomes the canonical owner and the other is
removed or documented as a reconciled projection.
