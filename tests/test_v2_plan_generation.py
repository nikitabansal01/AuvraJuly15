"""Contract tests for the provider-neutral v2 plan-generation vertical slice."""

from __future__ import annotations

import base64
import hashlib
import io
import json
from dataclasses import asdict
from typing import Any, Mapping, Sequence

import httpx
import pytest
from PIL import Image

from app.v2.application.plan_generation import (
    AiInvocationMetadata,
    GeneratedImage,
    PlanGenerationOrchestrator,
    PlanGenerationRequest,
    ProviderFailure,
    StoredMedia,
    StructuredPlanResponse,
)
from app.v2.domain.plan_generation import (
    EvidenceSource,
    PlanCandidateRejected,
    candidate_from_payload,
    validate_candidate_evidence,
)
from app.v2.domain.plan_image_prompts import sanitize_image_prompt
from app.v2.domain.plan_evidence import (
    assessment_context_from_answers,
    evidence_queries_for,
)
from app.v2.infrastructure.plan_generation_openai import (
    OpenAIStructuredPlanGateway,
)
from app.v2.infrastructure.plan_generation_providers import (
    CloudflareFluxImageGateway,
    GeminiStructuredPlanGateway,
    PubmedEvidenceResolver,
    SupabasePermanentMediaStore,
)
from app.v2.infrastructure.deterministic_plan_generation import (
    DeterministicEvidenceResolver,
    DeterministicImageGenerator,
    DeterministicStructuredPlanGateway,
)


SOURCE_URL = "https://pubmed.ncbi.nlm.nih.gov/12345678/"


def _png_bytes(color: tuple[int, int, int] = (1, 2, 3)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


PNG_BYTES = _png_bytes()


def _payload(citation_url: str = SOURCE_URL) -> dict[str, Any]:
    return {
        "actions": [
            {
                "category": "movement",
                "title": f"Action {slot}",
                "purpose": "Supports a consistent, gentle routine.",
                "instructions": [
                    "Choose a comfortable pace.",
                    "Stop if it feels unwell.",
                ],
                "image_prompt": f"A calm wellbeing activity {slot}",
                "citation_urls": [citation_url],
                "variants": [
                    {
                        "variant_type": variant_type,
                        "title": f"{variant_type} action {slot}",
                        "instructions": ["Use the safer shorter form."],
                        "image_prompt": f"A calm {variant_type} activity {slot}",
                    }
                    for variant_type in ("low_energy", "time_limited", "no_equipment")
                ],
            }
            for slot in range(1, 5)
        ]
    }


def _invocation() -> AiInvocationMetadata:
    return AiInvocationMetadata(
        provider="deterministic",
        operation="test",
        task="plan_generation",
        prompt_version="plan.v1",
        model="deterministic-test",
        input_tokens=0,
        output_tokens=0,
        cost_minor=0,
        latency_ms=0,
        result_status="succeeded",
        input_hash="a" * 64,
        output_hash="b" * 64,
    )


class _PlanGateway:
    async def generate(self, **_: Any) -> StructuredPlanResponse:
        return StructuredPlanResponse(content=_payload(), invocation=_invocation())


class _EvidenceResolver:
    async def resolve(self, query: str) -> Sequence[EvidenceSource]:
        return [EvidenceSource(canonical_url=SOURCE_URL, title=f"Evidence for {query}")]


class _ImageGateway:
    async def generate(self, *, prompt: str) -> GeneratedImage:
        digest = hashlib.sha256(prompt.encode("utf-8")).digest()
        return GeneratedImage(
            content=_png_bytes(tuple(digest[:3])),
            mime_type="image/png",
            invocation=AiInvocationMetadata(
                **{
                    **asdict(_invocation()),
                    "provider": "cloudflare_workers_ai",
                    "task": "plan_image_generation",
                }
            ),
        )


class _MediaStore:
    def __init__(self, fail_at: int | None = None) -> None:
        self.fail_at = fail_at
        self.put_calls = 0
        self.deleted: list[str] = []

    async def put(self, *, content: bytes, mime_type: str, object_key: str) -> StoredMedia:
        self.put_calls += 1
        if self.fail_at == self.put_calls:
            raise ProviderFailure("storage_timeout", retryable=True)
        return StoredMedia(
            provider="test",
            bucket="plans",
            object_key=object_key,
            public_url=f"https://assets.example/{object_key}",
            content_sha256=hashlib.sha256(content).hexdigest(),
            mime_type=mime_type,
            width=1,
            height=1,
        )

    async def delete(self, *, object_key: str) -> None:
        self.deleted.append(object_key)


def _orchestrator(store: _MediaStore) -> PlanGenerationOrchestrator:
    return PlanGenerationOrchestrator(
        plan_gateway=_PlanGateway(),
        evidence_resolver=_EvidenceResolver(),
        image_generator=_ImageGateway(),
        media_store=store,
    )


@pytest.mark.anyio
async def test_orchestration_returns_exactly_sixteen_permanent_assets_without_raw_context():
    store = _MediaStore()
    bundle = await _orchestrator(store).generate(
        PlanGenerationRequest(
            task="plan_generation",
            prompt_version="plan.v1",
            request_context={"private_health_answer": "do not retain this"},
            evidence_queries=("gentle routine",),
        )
    )

    assert len(bundle.candidate.actions) == 4
    assert len(bundle.assets) == 16
    assert len({asset.media.content_sha256 for asset in bundle.assets}) == 16
    assert all(asset.media.public_url.startswith("https://") for asset in bundle.assets)
    assert all(asset.media.object_key.endswith(".png") for asset in bundle.assets)
    assert len(bundle.invocations) == 17
    assert "private_health_answer" not in str(asdict(bundle.invocation))
    assert "do not retain this" not in str(asdict(bundle.invocation))


@pytest.mark.anyio
async def test_media_object_keys_are_namespaced_by_the_generation_job():
    first = await _orchestrator(_MediaStore()).generate(
        PlanGenerationRequest(
            "plan_generation", "plan.v1", {}, ("routine",), generation_job_id="job-a"
        )
    )
    second = await _orchestrator(_MediaStore()).generate(
        PlanGenerationRequest(
            "plan_generation", "plan.v1", {}, ("routine",), generation_job_id="job-b"
        )
    )
    assert "/job-a/" in first.assets[0].media.object_key
    assert "/job-b/" in second.assets[0].media.object_key


@pytest.mark.anyio
async def test_deterministic_adapters_exercise_the_full_orchestration_contract():
    bundle = await PlanGenerationOrchestrator(
        plan_gateway=DeterministicStructuredPlanGateway(),
        evidence_resolver=DeterministicEvidenceResolver(),
        image_generator=DeterministicImageGenerator(),
        media_store=_MediaStore(),
    ).generate(
        PlanGenerationRequest("plan_generation", "plan.v1", {"private": "answer"}, ("routine",))
    )

    assert len(bundle.assets) == 16
    assert bundle.invocation.provider == "deterministic-test"


@pytest.mark.anyio
async def test_partial_uploads_are_deleted_when_a_later_upload_fails():
    store = _MediaStore(fail_at=3)

    with pytest.raises(ProviderFailure, match="storage_timeout"):
        await _orchestrator(store).generate(
            PlanGenerationRequest(
                task="plan_generation",
                prompt_version="plan.v1",
                request_context={},
                evidence_queries=("gentle routine",),
            )
        )

    assert store.put_calls == 3
    assert len(store.deleted) == 2


@pytest.mark.anyio
async def test_unverified_citations_fail_before_media_generation():
    class UnverifiedGateway(_PlanGateway):
        async def generate(self, **_: Any) -> StructuredPlanResponse:
            return StructuredPlanResponse(
                content=_payload("https://unverified.example/source"),
                invocation=_invocation(),
            )

    store = _MediaStore()
    orchestrator = PlanGenerationOrchestrator(
        plan_gateway=UnverifiedGateway(),
        evidence_resolver=_EvidenceResolver(),
        image_generator=_ImageGateway(),
        media_store=store,
    )
    with pytest.raises(PlanCandidateRejected, match="candidate_evidence_unretrieved_citation"):
        await orchestrator.generate(
            PlanGenerationRequest("plan_generation", "plan.v1", {}, ("gentle routine",))
        )
    assert store.put_calls == 0


def test_medical_claim_and_variant_shape_are_rejected():
    unsafe = _payload()
    unsafe["actions"][0]["purpose"] = "This will diagnose your condition."
    with pytest.raises(PlanCandidateRejected, match="candidate_safety_diagnostic"):
        candidate_from_payload(unsafe)

    invalid_variants = _payload()
    invalid_variants["actions"][0]["variants"] = invalid_variants["actions"][0]["variants"][:2]
    with pytest.raises(PlanCandidateRejected, match="candidate_shape"):
        candidate_from_payload(invalid_variants)


@pytest.mark.parametrize(
    ("field", "unsafe_text", "reason_code"),
    [
        ("title", "Diagnose your condition", "candidate_safety_diagnostic"),
        (
            "instructions",
            ["Take a medication every day"],
            "candidate_safety_medication",
        ),
        ("image_prompt", "An emergency room scene", "candidate_safety_emergency"),
        ("purpose", "Helps with chest pain", "candidate_safety_red_flag"),
        (
            "image_prompt",
            "Avoid this if you have a condition",
            "candidate_safety_contraindication",
        ),
    ],
)
def test_safety_policy_rejects_each_prohibited_category_in_visible_fields(
    field: str, unsafe_text: object, reason_code: str
) -> None:
    payload = _payload()
    payload["actions"][0][field] = unsafe_text
    with pytest.raises(PlanCandidateRejected) as raised:
        candidate_from_payload(payload)
    assert raised.value.reason_code == reason_code
    assert raised.value.retryable is False
    assert raised.value.policy_version == "plan-safety.v2"


def test_safety_policy_covers_variant_titles_instructions_and_prompts() -> None:
    for field, unsafe_text in (
        ("title", "Prescription routine"),
        ("instructions", ["Call an ambulance"]),
        ("image_prompt", "A red flag warning poster"),
    ):
        payload = _payload()
        payload["actions"][0]["variants"][0][field] = unsafe_text
        with pytest.raises(PlanCandidateRejected) as raised:
            candidate_from_payload(payload)
        assert raised.value.reason_code.startswith("candidate_safety_")


def test_safe_candidate_and_deterministic_evidence_relevance_are_accepted() -> None:
    candidate = candidate_from_payload(_payload())
    validate_candidate_evidence(
        candidate,
        [EvidenceSource(canonical_url=SOURCE_URL, title="Verified gentle routine study")],
        ("gentle routine wellbeing",),
    )


def test_evidence_relevance_failure_is_terminal_and_does_not_claim_entailment() -> None:
    candidate = candidate_from_payload(_payload())
    with pytest.raises(PlanCandidateRejected) as raised:
        validate_candidate_evidence(
            candidate,
            [EvidenceSource(canonical_url=SOURCE_URL, title="Verified source")],
            ("unrelated astronomy observations",),
        )
    assert raised.value.reason_code == "candidate_evidence_action_query_irrelevant"
    assert raised.value.retryable is False


def test_irrelevant_citation_title_is_rejected_even_when_url_and_action_match() -> None:
    candidate = candidate_from_payload(_payload())
    with pytest.raises(PlanCandidateRejected) as raised:
        validate_candidate_evidence(
            candidate,
            [EvidenceSource(canonical_url=SOURCE_URL, title="Astronomy telescope observations")],
            ("gentle routine wellbeing",),
        )
    assert raised.value.reason_code == "candidate_evidence_citation_title_irrelevant"


def test_candidate_rejection_codes_are_snake_case_and_never_messages() -> None:
    rejection = PlanCandidateRejected("candidate_shape_actions")
    assert rejection.reason_code == "candidate_shape_actions"
    with pytest.raises(ValueError, match="plan_candidate_rejection_code_invalid"):
        PlanCandidateRejected("a human-readable error message")


def test_image_prompt_sanitizer_never_returns_model_or_identifying_text() -> None:
    raw_prompt = "Portrait of Priya Patel, phone 555-0100, taking medication"
    sanitized = sanitize_image_prompt(raw_prompt)
    assert "Priya" not in sanitized
    assert "555" not in sanitized
    assert "medication" not in sanitized
    assert "no identifiable person" in sanitized


def test_evidence_queries_are_typed_bounded_and_ignore_free_text() -> None:
    context = assessment_context_from_answers(
        {
            "lifestyle_focus": ["move", "pause", "unrecognized"],
            "workout_intensity": "I'm yet to start",
            "sleep_duration": "<6 hours",
            "other_concerns": ["My name is Alex and my phone is 555-0100"],
            "diagnosed_conditions": ["Others: secret diagnosis"],
        }
    )
    queries = evidence_queries_for(context)
    assert queries == (
        "physical activity adult wellbeing",
        "stress management adult wellbeing",
        "physical activity beginner adult wellbeing",
        "sleep hygiene adult wellbeing",
    )
    assert "Alex" not in str(context.provider_context(timezone="UTC", local_date="2026-08-08"))
    assert "secret diagnosis" not in " ".join(queries)


@pytest.mark.anyio
async def test_gemini_adapter_requests_structured_output_and_redacts_telemetry():
    observed: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["request"] = json.loads(request.content)
        observed["url"] = str(request.url)
        observed["api_key"] = request.headers.get("x-goog-api-key")
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": json.dumps(_payload())}],
                    }
                ],
                "usage": {"total_input_tokens": 7, "total_output_tokens": 11},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = GeminiStructuredPlanGateway(
        api_key="key",
        model="gemini-test",
        telemetry_hmac_key=b"t" * 32,
        client=client,
    )
    response = await gateway.generate(
        task="plan_generation",
        prompt_version="plan.v1",
        context={"private": "health answer"},
        evidence=[EvidenceSource(canonical_url=SOURCE_URL, title="Evidence")],
    )
    await client.aclose()

    assert response.content["actions"][0]["title"] == "Action 1"
    assert response.invocation.input_tokens == 7
    assert "private" not in str(asdict(response.invocation))
    assert "health answer" not in str(asdict(response.invocation))
    assert observed["request"]["store"] is False
    assert observed["request"]["response_format"]["mime_type"] == "application/json"
    assert observed["api_key"] == "key"
    assert "key=" not in observed["url"]


@pytest.mark.anyio
async def test_openai_adapter_requests_strict_structured_output_and_normalizes_key():
    observed: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["request"] = json.loads(request.content)
        observed["url"] = str(request.url)
        observed["authorization"] = request.headers.get("authorization")
        wellbeing = _payload()
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(wellbeing)}}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 13},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = OpenAIStructuredPlanGateway(
        api_key="key",
        model="gpt-test",
        telemetry_hmac_key=b"t" * 32,
        client=client,
    )
    response = await gateway.generate(
        task="plan_generation",
        prompt_version="plan.v1",
        context={"private": "health answer"},
        evidence=[EvidenceSource(canonical_url=SOURCE_URL, title="Evidence")],
    )
    await client.aclose()

    assert response.content["actions"][0]["title"] == "Action 1"
    assert response.invocation.input_tokens == 9
    assert response.invocation.provider == "openai"
    assert "private" not in str(asdict(response.invocation))
    assert "health answer" not in str(asdict(response.invocation))
    assert observed["authorization"] == "Bearer key"
    assert "key=" not in observed["url"]
    schema = observed["request"]["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False


@pytest.mark.anyio
async def test_openai_adapter_tolerates_wellbeing_actions_key_and_bad_json():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps({"wellbeing_actions": _payload()["actions"]})
                            }
                        }
                    ],
                    "usage": {},
                },
            )
        )
    )
    gateway = OpenAIStructuredPlanGateway(
        api_key="key",
        model="gpt-test",
        telemetry_hmac_key=b"t" * 32,
        client=client,
    )
    response = await gateway.generate(
        task="plan_generation",
        prompt_version="plan.v1",
        context={},
        evidence=[EvidenceSource(canonical_url=SOURCE_URL, title="Evidence")],
    )
    await client.aclose()
    assert response.content["actions"][0]["title"] == "Action 1"

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": "not json"}}], "usage": {}},
            )
        )
    )
    gateway = OpenAIStructuredPlanGateway(
        api_key="key",
        model="gpt-test",
        telemetry_hmac_key=b"t" * 32,
        client=client,
    )
    with pytest.raises(ProviderFailure) as exc_info:
        await gateway.generate(
            task="plan_generation",
            prompt_version="plan.v1",
            context={},
            evidence=[EvidenceSource(canonical_url=SOURCE_URL, title="Evidence")],
        )
    await client.aclose()
    assert exc_info.value.code == "openai_invalid_json"
    assert exc_info.value.retryable is True


@pytest.mark.anyio
async def test_cloudflare_and_storage_adapters_preserve_image_integrity():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "/ai/run/" in str(request.url):
            return httpx.Response(200, content=PNG_BYTES, headers={"content-type": "image/png"})
        return httpx.Response(200, json={"Key": "stored"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    image = await CloudflareFluxImageGateway(
        account_id="account", api_token="token", client=client
    ).generate(prompt="a calm image")
    media = await SupabasePermanentMediaStore(
        project_url="https://project.supabase.co",
        service_role_key="service-key",
        bucket="plan-images",
        client=client,
    ).put(
        content=image.content,
        mime_type=image.mime_type,
        object_key="plans/v2/example.png",
    )
    await client.aclose()

    assert image.mime_type == "image/png"
    assert media.width == 1 and media.height == 1
    assert media.content_sha256 == hashlib.sha256(PNG_BYTES).hexdigest()
    assert requests[1].headers["x-upsert"] == "false"
    assert "a calm image" not in requests[0].content.decode("utf-8")
    assert "no identifiable person" in requests[0].content.decode("utf-8")


@pytest.mark.anyio
async def test_content_addressed_storage_recovers_from_an_existing_immutable_object():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(400, json={"message": "Asset Already Exists"})
        return httpx.Response(200, content=PNG_BYTES, headers={"content-type": "image/png"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = SupabasePermanentMediaStore(
        project_url="https://project.supabase.co",
        service_role_key="service-key",
        bucket="plan-images",
        client=client,
    )
    media = await store.put(
        content=PNG_BYTES,
        mime_type="image/png",
        object_key="plans/v2/existing.png",
    )
    await client.aclose()

    assert media.public_url.endswith("/plan-images/plans/v2/existing.png")
    assert [request.method for request in requests] == ["POST", "GET"]


@pytest.mark.anyio
async def test_pubmed_resolver_accepts_only_official_eutilities_records():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"idlist": ["12345678"]}})
        return httpx.Response(
            200,
            json={"result": {"12345678": {"title": "Verified study", "pubdate": "2025"}}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resolver = PubmedEvidenceResolver(tool="auvra", email="ops@example.test", client=client)
    sources = await resolver.resolve("wellbeing lifestyle intervention")
    await client.aclose()
    assert sources == (
        EvidenceSource(canonical_url=SOURCE_URL, title="Verified study", published_date="2025"),
    )
