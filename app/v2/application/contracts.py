"""Stable command and result types exposed by the v2 application boundary."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from app.v2.domain.enums import JobState, MediaStatus, PlanStatus


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(ContractModel):
    status: str
    service: str
    version: str


class ProblemDetail(BaseModel):
    """RFC 9457 base fields plus stable AUVRA error identifiers."""

    model_config = ConfigDict(extra="allow")

    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    request_id: str


class ConsentRequirement(ContractModel):
    """The server-selected consent document a claimant must decide on."""

    consent_type: str = Field(min_length=1, max_length=64)
    document_version: str = Field(min_length=1, max_length=64)


class OnboardingSessionResponse(ContractModel):
    session_id: uuid.UUID
    proof_token: str
    expires_at: datetime
    required_consents: list[ConsentRequirement]


PeriodDescription = Literal[
    "Regular",
    "Irregular",
    "Occasional Skips",
    "I don't get periods",
    "I'm not sure",
]
CycleLength = Literal[
    "Less than 21 days",
    "21-25 days",
    "26-30 days",
    "31-35 days",
    "35+ days",
    "I'm not sure",
]
BirthControl = Literal[
    "Hormonal Birth Control Pills",
    "IUD (Intrauterine Device)",
    "Copper IUD (Intrauterine Device)",
]
PeriodConcern = Literal[
    "Irregular Periods",
    "Painful Periods",
    "Light periods / Spotting",
    "Heavy periods",
]
BodyConcern = Literal[
    "Bloating",
    "Hot Flashes",
    "Nausea",
    "Difficulty losing weight / stubborn belly fat",
    "Recent weight gain",
    "Menstrual headaches",
]
SkinHairConcern = Literal[
    "Hirsutism (hair growth on chin, nipples etc)",
    "Thinning of hair",
    "Adult Acne",
]
MentalHealthConcern = Literal["Mood swings", "Stress", "Fatigue"]
Diagnosis = Literal[
    "PCOS",
    "PCOD",
    "Endometriosis",
    "Dysmenorrhea",
    "Amenorrhea",
    "Menorrhagia",
    "Metrorrhagia",
    "Cushing's Syndrome",
    "Premenstrual Syndrome",
    "Diabetes",
    "PMDD",
    "Hashimoto's",
    "Hypothyroidism",
    "None of the above",
    "Others (please specify)",
]


class MobileQuestionnaireV1(ContractModel):
    """The documented answer keys emitted by the mobile QuestionScreen."""

    age: int | None = Field(default=None, ge=13, le=120)
    period_description: PeriodDescription | None = None
    birth_control: list[BirthControl] | None = Field(default=None, max_length=3)
    last_period_date: date | None = None
    cycle_length: CycleLength | None = None
    period_concerns: list[PeriodConcern] | None = Field(default=None, max_length=4)
    body_concerns: list[BodyConcern] | None = Field(default=None, max_length=6)
    skin_hair_concerns: list[SkinHairConcern] | None = Field(default=None, max_length=3)
    mental_health_concerns: list[MentalHealthConcern] | None = Field(
        default=None, max_length=3
    )
    other_concerns: list[
        Annotated[str, Field(min_length=1, max_length=200)]
    ] | None = Field(default=None, max_length=2)
    top_concern: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    diagnosed_conditions: list[
        Annotated[str, Field(min_length=1, max_length=200)]
    ] | None = Field(default=None, max_length=14)
    family_history: list[
        Annotated[str, Field(min_length=1, max_length=200)]
    ] | None = Field(default=None, max_length=14)
    workout_intensity: Literal[
        "Low", "Moderate", "High", "I'm yet to start"
    ] | None = None
    sleep_duration: Literal[
        "<6 hours", "6-7 hours", "7-8 hours", "8+ hours"
    ] | None = None
    stress_level: Literal["Low", "Moderate", "High"] | None = None
    lifestyle_focus: list[Literal["eat", "move", "pause"]] | None = Field(
        default=None,
        max_length=3,
    )

    @model_validator(mode="after")
    def values_are_documented(self) -> MobileQuestionnaireV1:
        answer_values = self.model_dump(exclude_none=True)
        if not answer_values:
            raise ValueError("answers must include at least one documented question")
        _validate_unique_lists(answer_values)
        _validate_other_values(
            self.other_concerns, {"None of these", "Others (please specify)"}
        )
        _validate_other_values(self.diagnosed_conditions, set(Diagnosis.__args__))
        _validate_other_values(self.family_history, set(Diagnosis.__args__))
        _validate_top_concern(self.top_concern)
        return self


def _validate_unique_lists(answer_values: dict[str, object]) -> None:
    for key, value in answer_values.items():
        if isinstance(value, list) and len(set(value)) != len(value):
            raise ValueError(f"{key} must not contain duplicate values")


def _validate_other_values(values: list[str] | None, allowed: set[str]) -> None:
    if values is None:
        return
    for value in values:
        if value not in allowed and not value.startswith("Others:"):
            raise ValueError("custom values must use the 'Others:' prefix")


def _validate_top_concern(value: str | None) -> None:
    if value is None:
        return
    allowed = set(
        PeriodConcern.__args__ + BodyConcern.__args__ + SkinHairConcern.__args__
    )
    allowed.update(MentalHealthConcern.__args__)
    if value not in allowed and not value.startswith("Others:"):
        raise ValueError(
            "top_concern must be a documented concern or use the 'Others:' prefix"
        )


class AssessmentWriteRequest(ContractModel):
    schema_version: Literal["mobile-questionnaire.v1"]
    timezone: str = Field(min_length=1, max_length=64)
    answers: MobileQuestionnaireV1

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value


class AssessmentResponse(ContractModel):
    assessment_id: uuid.UUID
    session_id: uuid.UUID
    version: int
    schema_version: str
    timezone: str
    validated_at: datetime


class ConsentDecision(ContractModel):
    consent_type: str = Field(min_length=1, max_length=64)
    document_version: str = Field(min_length=1, max_length=64)
    granted: bool


class ClaimOnboardingRequest(ContractModel):
    consents: list[ConsentDecision] = Field(min_length=2, max_length=10)

    @model_validator(mode="after")
    def required_consents(self) -> ClaimOnboardingRequest:
        decisions = {item.consent_type: item.granted for item in self.consents}
        required = {"privacy", "health_data_processing"}
        if not required.issubset(decisions):
            raise ValueError(
                "privacy and health_data_processing consent decisions are required"
            )
        if not all(decisions[name] for name in required):
            raise ValueError("required consents must be granted before claim")
        if len(decisions) != len(self.consents):
            raise ValueError("consent_type values must be unique")
        return self


class ProfileResponse(ContractModel):
    user_id: uuid.UUID
    email: str | None
    email_verified: bool
    display_name: str | None
    timezone: str
    locale: str
    version: int
    created_at: datetime
    updated_at: datetime


class ClaimOnboardingResponse(ContractModel):
    user_id: uuid.UUID
    assessment_id: uuid.UUID
    profile: ProfileResponse


class ProfilePatchRequest(ContractModel):
    display_name: str | None = Field(default=None, max_length=160)
    timezone: str | None = Field(default=None, max_length=64)
    locale: str | None = Field(default=None, min_length=2, max_length=16)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def at_least_one_field(self) -> ProfilePatchRequest:
        if not self.model_fields_set:
            raise ValueError("at least one profile field is required")
        return self


class PlanGenerationRequest(ContractModel):
    local_date: date | None = None


class JobResponse(ContractModel):
    job_id: uuid.UUID
    job_type: str
    state: JobState
    progress: int
    phase: str | None
    # Non-plan durable jobs do not have a user-local plan date.
    local_date: date | None = None
    created_at: datetime
    updated_at: datetime
    error_code: str | None = None


class MediaAssetResponse(ContractModel):
    asset_id: uuid.UUID
    public_url: HttpUrl
    alt_text: str
    mime_type: str
    width: int | None
    height: int | None
    status: MediaStatus


class PlanVariantResponse(ContractModel):
    variant_id: uuid.UUID
    variant_type: str
    content: dict[str, Any]
    image: MediaAssetResponse


class PlanItemResponse(ContractModel):
    item_id: uuid.UUID
    slot: int
    category: str
    title: str
    purpose: str
    instructions: dict[str, Any]
    hero_image: MediaAssetResponse
    variants: list[PlanVariantResponse]


class CurrentPlanResponse(ContractModel):
    plan_id: uuid.UUID
    revision: int
    status: PlanStatus
    local_date: date
    timezone: str
    cycle_snapshot: dict[str, Any]
    items: list[PlanItemResponse]
    published_at: datetime


class PlanReplacementRequest(ContractModel):
    """Replace one item by promoting one of its already-published variants.

    This is deliberately a selection command, not an implicit AI regeneration
    request.  Provider-backed replacement remains a separate durable workflow
    until an owner-approved runner exists.
    """

    item_id: uuid.UUID
    selected_variant_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_:-]+$")


class PlanReplacementResponse(ContractModel):
    refresh_id: uuid.UUID
    plan_id: uuid.UUID
    revision: int
    local_date: date
    timezone: str
    old_item_id: uuid.UUID
    new_item_id: uuid.UUID
    replacement_mode: Literal["selected_variant"]


ActionEventType = Literal["completed", "skipped", "feedback"]


class ActionEventRequest(ContractModel):
    client_event_id: uuid.UUID
    event_type: ActionEventType
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class ActionEventResponse(ContractModel):
    event_id: uuid.UUID
    plan_item_id: uuid.UUID
    event_type: ActionEventType
    occurred_at: datetime
    decision_local_date: date
    decision_timezone: str


class DailyReviewItemInput(ContractModel):
    """One immutable answer for one item in the reviewed plan revision."""

    plan_item_id: uuid.UUID
    outcome: Literal["completed", "skipped", "not_done"]
    note: str | None = Field(default=None, max_length=2000)


class DailyReviewRequest(ContractModel):
    """A complete review; partial review writes intentionally do not exist."""

    items: list[DailyReviewItemInput] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def each_item_is_answered_once(self) -> DailyReviewRequest:
        item_ids = [item.plan_item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("each plan item may be reviewed only once")
        return self


class DailyReviewResponse(ContractModel):
    review_id: uuid.UUID
    plan_id: uuid.UUID
    local_date: date
    timezone: str
    status: Literal["completed"]
    completed_count: int
    total_count: int
    streak_state: Literal["earned", "missed"]
    reward_points_granted: int


ConversationThreadType = Literal[
    "general", "care_plan", "weekly_checkin", "symptom_checkin", "support"
]
ConversationStatus = Literal["active", "closed"]


class ConversationCreateRequest(ContractModel):
    thread_type: ConversationThreadType = "general"


class ConversationResponse(ContractModel):
    conversation_id: uuid.UUID
    thread_type: ConversationThreadType
    status: ConversationStatus
    revision: int
    created_at: datetime
    updated_at: datetime


class ConversationMessageResponse(ContractModel):
    message_id: uuid.UUID
    sequence: int
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: datetime


class ConversationDetailResponse(ConversationResponse):
    messages: list[ConversationMessageResponse]
    next_message_cursor: str | None = None


class ConversationPageResponse(ContractModel):
    conversations: list[ConversationResponse]
    next_cursor: str | None = None


class ConversationMessageCreateRequest(ContractModel):
    client_message_id: uuid.UUID
    content: str = Field(min_length=1, max_length=8000)


class ConversationMessageAcceptedResponse(ContractModel):
    message: ConversationMessageResponse
    conversation_id: uuid.UUID
    conversation_revision: int
    response_job_id: uuid.UUID
    job_state: Literal["queued"]


class WeeklyCheckinQuestionResponse(ContractModel):
    question_id: uuid.UUID
    ordinal: int
    prompt: str
    answer_type: Literal["scale", "choice", "text", "boolean", "multi_select"]
    answer_schema: dict[str, Any]
    required: bool


class WeeklyCheckinSavedAnswerResponse(ContractModel):
    question_id: uuid.UUID
    answer: dict[str, Any]
    answered_at: datetime


class WeeklyCheckinResponse(ContractModel):
    checkin_id: uuid.UUID
    conversation_id: uuid.UUID
    week_start: date
    definition_version: str
    timezone: str
    revision: int
    completed_at: datetime | None
    questions: list[WeeklyCheckinQuestionResponse]
    answers: list[WeeklyCheckinSavedAnswerResponse]


class WeeklyCheckinDueResponse(ContractModel):
    due: bool
    week_start: date
    timezone: str
    checkin: WeeklyCheckinResponse | None = None


class WeeklyCheckinAnswerRequest(ContractModel):
    answer: dict[str, Any] = Field(min_length=1)


class WeeklyCheckinAnswerResponse(ContractModel):
    checkin_id: uuid.UUID
    question_id: uuid.UUID
    revision: int
    completed_at: datetime | None
    answered_at: datetime


class SymptomObservationRequest(ContractModel):
    observed_at: datetime
    symptom_code: str = Field(min_length=1, max_length=64)
    severity: int | None = Field(default=None, ge=0, le=10)
    note: str | None = Field(default=None, max_length=4000)


class SymptomObservationResponse(ContractModel):
    observation_id: uuid.UUID


class ProgressSummaryResponse(ContractModel):
    local_date: date
    completed_today: int
    eligible_today: int
    daily_adherence: float | None
    is_current_day_provisional: bool
    streak_days: int
    reward_points: int
    refreshes_used: int


class DeletionResponse(ContractModel):
    deletion_request_id: uuid.UUID
    job_id: uuid.UUID
    state: str


class AccountExportResponse(ContractModel):
    export_id: uuid.UUID
    job_id: uuid.UUID
    state: str
    expires_at: datetime


class AccountDeletionStatusResponse(ContractModel):
    deletion_request_id: uuid.UUID
    state: str
    receipt_available: bool


class RewardBalance(ContractModel):
    """A computed balance. Never stored; always a sum over the ledger."""

    asset_type: str
    asset_key: str | None
    balance: int


class RewardGrant(ContractModel):
    asset_type: str
    asset_key: str | None
    quantity: int


class RewardState(ContractModel):
    reward_id: str
    title: str
    category: str
    effect: str
    icon: str
    required_streak_days: int
    state: str
    claimed_at: datetime | None


class RewardsOverviewResponse(ContractModel):
    catalog_version: str
    current_streak_days: int
    best_streak_days: int
    balances: list[RewardBalance]
    rewards: list[RewardState]


class RewardClaimResponse(ContractModel):
    reward_id: str
    catalog_version: str
    granted: list[RewardGrant]
    claimed_at: datetime


class StreakFreezeRequest(ContractModel):
    """Freezes one already-closed local day the user would otherwise lose."""

    local_date: date


class StreakFreezeResponse(ContractModel):
    local_date: date
    timezone: str
    streak_day_id: uuid.UUID
    freezes_remaining: int
    streak_days: int


class ObservationValue(ContractModel):
    """Exactly one of these is set, mirroring the database's typed columns."""

    numeric: float | None = None
    unit: str | None = None
    codes: list[str] | None = None
    text: str | None = Field(default=None, max_length=4000)


class ObservationWriteRequest(ContractModel):
    client_observation_id: uuid.UUID
    observation_type: str = Field(min_length=1, max_length=24)
    code: str = Field(min_length=1, max_length=64)
    observed_at: datetime
    value: ObservationValue
    note: str | None = Field(default=None, max_length=4000)
    #: Corrections cite the assertion they replace instead of rewriting it.
    supersedes_observation_id: uuid.UUID | None = None


class ObservationResponse(ContractModel):
    observation_id: uuid.UUID
    observation_type: str
    code: str
    catalog_version: str
    observed_at: datetime
    observed_local_date: date
    value: ObservationValue
    note: str | None
    supersedes_observation_id: uuid.UUID | None
    recorded_at: datetime


class ObservationPageResponse(ContractModel):
    observations: list[ObservationResponse]
    next_cursor: uuid.UUID | None


class DerivedBodyMetrics(ContractModel):
    """Computed on read. Never stored, so it cannot go stale."""

    bmi: float | None
    bmi_band: str | None
    waist_height_ratio: float | None


class CurrentObservationsResponse(ContractModel):
    entries: list[ObservationResponse]
    #: Personalization codes this user has earned; the client greys out the
    #: rest without needing a second request.
    unlocked_codes: list[str]
    derived: DerivedBodyMetrics


class ObservationCatalogEntry(ContractModel):
    code: str
    observation_type: str
    value_kind: str
    label: str
    unit: str | None
    minimum: float | None
    maximum: float | None
    choices: list[str]
    multi_select: bool


class ObservationCatalogResponse(ContractModel):
    catalog_version: str
    entries: list[ObservationCatalogEntry]


class CycleStateResponse(ContractModel):
    """Derived on read from observed period starts; nothing here is stored."""

    as_of_local_date: date
    timezone: str
    policy_version: str
    cycle_day: int | None
    phase: str | None
    phase_confidence: str
    cycle_length_days: int | None
    #: Whether cycle length came from the user's own history or her report.
    cycle_length_source: str
    last_period_start: date | None
    next_period_estimate: date | None
