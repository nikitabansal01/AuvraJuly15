"""HTTP adapters for the v2 plan-generation pipeline.

These adapters use documented REST interfaces directly.  Keeping the protocol
details here prevents provider SDKs, request credentials, and error formats
from leaking into the domain or API layers.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import time
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

import httpx
from PIL import Image

from app.v2.application.plan_generation import (
    GeneratedImage,
    ProviderFailure,
    StoredMedia,
    StructuredPlanResponse,
    invocation_metadata,
)
from app.v2.application.conversation_response import (
    ConversationGatewayResult,
    ConversationResponseRequest,
)
from app.v2.domain.plan_generation import CANONICAL_VARIANT_TYPES, EvidenceSource
from app.v2.domain.plan_image_prompts import sanitize_image_prompt


GEMINI_STRUCTURED_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "title": {"type": "string"},
                    "purpose": {"type": "string"},
                    "instructions": {"type": "array", "items": {"type": "string"}},
                    "image_prompt": {"type": "string"},
                    "citation_urls": {"type": "array", "items": {"type": "string"}},
                    "variants": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {
                            "type": "object",
                            "properties": {
                                "variant_type": {
                                    "type": "string",
                                    "enum": list(CANONICAL_VARIANT_TYPES),
                                },
                                "title": {"type": "string"},
                                "instructions": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "image_prompt": {"type": "string"},
                            },
                            "required": [
                                "variant_type",
                                "title",
                                "instructions",
                                "image_prompt",
                            ],
                        },
                    },
                },
                "required": [
                    "category",
                    "title",
                    "purpose",
                    "instructions",
                    "image_prompt",
                    "citation_urls",
                    "variants",
                ],
            },
        }
    },
    "required": ["actions"],
}


class GeminiStructuredPlanGateway:
    """Gemini REST adapter using JSON-schema structured output."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        telemetry_hmac_key: bytes,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key or not model:
            raise ValueError("Gemini API key and model are required")
        if len(telemetry_hmac_key) < 32:
            raise ValueError("telemetry_hmac_key must contain at least 32 bytes")
        self._api_key = api_key
        self._model = model
        self._telemetry_hmac_key = telemetry_hmac_key
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(90.0))
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def generate(
        self,
        *,
        task: str,
        prompt_version: str,
        context: Mapping[str, Any],
        evidence: Sequence[EvidenceSource],
    ) -> StructuredPlanResponse:
        request = _gemini_request(self._model, context, evidence)
        started_at = time.monotonic()
        response = await self._post(
            "https://generativelanguage.googleapis.com/v1/interactions",
            headers={"x-goog-api-key": self._api_key},
            json=request,
        )
        parsed = _gemini_plan_payload(response)
        usage = response.get("usage", {})
        return StructuredPlanResponse(
            content=parsed,
            invocation=invocation_metadata(
                provider="gemini",
                operation="create_interaction",
                task=task,
                prompt_version=prompt_version,
                model=self._model,
                request_payload=request,
                response_payload=parsed,
                telemetry_hmac_key=self._telemetry_hmac_key,
                started_at=started_at,
                input_tokens=_integer(usage.get("total_input_tokens")),
                output_tokens=_integer(usage.get("total_output_tokens")),
            ),
        )

    async def _post(self, url: str, **kwargs: Any) -> Mapping[str, Any]:
        try:
            response = await self._client.post(url, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderFailure("gemini_timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderFailure("gemini_network_error", retryable=True) from exc
        _raise_for_status(response, "gemini")
        try:
            value = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderFailure("gemini_invalid_response", retryable=False) from exc
        if not isinstance(value, Mapping):
            raise ProviderFailure("gemini_invalid_response", retryable=False)
        return value


class GeminiConversationGateway:
    """Gemini REST adapter for bounded, provider-neutral conversation requests."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        telemetry_hmac_key: bytes,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key or not model:
            raise ValueError("Gemini API key and model are required")
        if len(telemetry_hmac_key) < 32:
            raise ValueError("telemetry_hmac_key must contain at least 32 bytes")
        self._api_key, self._model = api_key, model
        self._telemetry_hmac_key = telemetry_hmac_key
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(90.0))
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def respond(
        self, request: ConversationResponseRequest
    ) -> ConversationGatewayResult:
        payload = _gemini_conversation_request(self._model, request)
        started_at = time.monotonic()
        try:
            response = await self._client.post(
                "https://generativelanguage.googleapis.com/v1/interactions",
                headers={"x-goog-api-key": self._api_key},
                json=payload,
            )
        except httpx.TimeoutException as exc:
            raise ProviderFailure("gemini_timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderFailure("gemini_network_error", retryable=True) from exc
        _raise_for_status(response, "gemini")
        try:
            value = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderFailure("gemini_invalid_response", retryable=False) from exc
        if not isinstance(value, Mapping):
            raise ProviderFailure("gemini_invalid_response", retryable=False)
        content = _gemini_conversation_text(value)
        usage = value.get("usage", {})
        return ConversationGatewayResult(
            content=content,
            invocation=invocation_metadata(
                provider="gemini",
                operation="create_interaction",
                task="conversation_response",
                prompt_version=request.prompt_version,
                model=self._model,
                request_payload=payload,
                response_payload={"content": content},
                telemetry_hmac_key=self._telemetry_hmac_key,
                started_at=started_at,
                input_tokens=_integer(usage.get("total_input_tokens")),
                output_tokens=_integer(usage.get("total_output_tokens")),
            ),
        )


class PubmedEvidenceResolver:
    """Resolve only official NCBI E-utilities records into canonical PMID URLs."""

    _search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    _summary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

    def __init__(
        self,
        *,
        tool: str,
        email: str,
        max_results: int = 5,
        min_interval_seconds: float = 0.34,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not tool or not email or max_results < 1 or min_interval_seconds <= 0:
            raise ValueError(
                "PubMed tool, email, result count, and rate limit are required"
            )
        self._tool = tool
        self._email = email
        self._max_results = max_results
        self._min_interval_seconds = min_interval_seconds
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        self._owns_client = client is None
        self._last_request_at = 0.0
        self._request_lock = __import__("asyncio").Lock()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def resolve(self, query: str) -> Sequence[EvidenceSource]:
        if not query.strip() or len(query) > 300:
            raise ProviderFailure("pubmed_invalid_query", retryable=False)
        search = await self._request(
            self._search_url,
            {
                "db": "pubmed",
                "term": query,
                "retmode": "json",
                "retmax": self._max_results,
            },
        )
        ids = search.get("esearchresult", {}).get("idlist", [])
        if not isinstance(ids, list) or not all(
            isinstance(item, str) and item.isdigit() for item in ids
        ):
            raise ProviderFailure("pubmed_invalid_response", retryable=False)
        if not ids:
            raise ProviderFailure("pubmed_no_results", retryable=False)
        summary = await self._request(
            self._summary_url,
            {"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
        )
        records = summary.get("result", {})
        if not isinstance(records, Mapping):
            raise ProviderFailure("pubmed_invalid_response", retryable=False)
        sources: list[EvidenceSource] = []
        for pmid in ids:
            record = records.get(pmid)
            if not isinstance(record, Mapping) or not isinstance(
                record.get("title"), str
            ):
                continue
            date_value = record.get("pubdate")
            sources.append(
                EvidenceSource(
                    canonical_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    title=record["title"].strip(),
                    published_date=date_value if isinstance(date_value, str) else None,
                )
            )
        if not sources:
            raise ProviderFailure("pubmed_no_usable_results", retryable=False)
        return tuple(sources)

    async def _request(
        self, url: str, params: Mapping[str, str | int]
    ) -> Mapping[str, Any]:
        async with self._request_lock:
            wait_for = self._min_interval_seconds - (
                time.monotonic() - self._last_request_at
            )
            if wait_for > 0:
                await __import__("asyncio").sleep(wait_for)
            try:
                response = await self._client.get(
                    url,
                    params={**params, "tool": self._tool, "email": self._email},
                )
            except httpx.TimeoutException as exc:
                raise ProviderFailure("pubmed_timeout", retryable=True) from exc
            except httpx.HTTPError as exc:
                raise ProviderFailure("pubmed_network_error", retryable=True) from exc
            self._last_request_at = time.monotonic()
        _raise_for_status(response, "pubmed")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderFailure("pubmed_invalid_response", retryable=False) from exc
        if not isinstance(payload, Mapping):
            raise ProviderFailure("pubmed_invalid_response", retryable=False)
        return payload


class CloudflareFluxImageGateway:
    """Workers AI FLUX adapter that accepts either binary or documented base64 output."""

    def __init__(
        self,
        *,
        account_id: str,
        api_token: str,
        model: str = "@cf/black-forest-labs/flux-1-schnell",
        telemetry_hmac_key: bytes | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not account_id or not api_token:
            raise ValueError("Cloudflare account ID and API token are required")
        self._account_id = account_id
        self._api_token = api_token
        self._model = model
        self._telemetry_hmac_key = telemetry_hmac_key
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(90.0))
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def generate(self, *, prompt: str) -> GeneratedImage:
        url = (
            f"https://api.cloudflare.com/client/v4/accounts/{self._account_id}/ai/run/"
            f"{self._model}"
        )
        started_at = time.monotonic()
        sanitized_prompt = sanitize_image_prompt(prompt)
        try:
            response = await self._client.post(
                url,
                headers={"Authorization": f"Bearer {self._api_token}"},
                json={"prompt": sanitized_prompt, "steps": 4},
            )
        except httpx.TimeoutException as exc:
            raise ProviderFailure("cloudflare_image_timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderFailure(
                "cloudflare_image_network_error", retryable=True
            ) from exc
        _raise_for_status(response, "cloudflare_image")
        content, _ = _cloudflare_image_content(response)
        if not content:
            raise ProviderFailure("cloudflare_image_invalid_response", retryable=False)
        _, _, mime_type = _inspect_image(content, "cloudflare_image_invalid_response")
        invocation = None
        if self._telemetry_hmac_key is not None:
            invocation = invocation_metadata(
                provider="cloudflare_workers_ai",
                operation="generate_image",
                task="plan_image_generation",
                prompt_version="plan-image.v1",
                model=self._model,
                request_payload={"prompt": sanitized_prompt},
                response_payload={
                    "content_sha256": hashlib.sha256(content).hexdigest()
                },
                telemetry_hmac_key=self._telemetry_hmac_key,
                started_at=started_at,
            )
        return GeneratedImage(
            content=content, mime_type=mime_type, invocation=invocation
        )


class SupabasePermanentMediaStore:
    """Store immutable plan media with integrity metadata in one Storage bucket."""

    def __init__(
        self,
        *,
        project_url: str,
        service_role_key: str,
        bucket: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not project_url.startswith("https://") or not service_role_key or not bucket:
            raise ValueError(
                "Supabase HTTPS URL, service-role key, and bucket are required"
            )
        self._project_url = project_url.rstrip("/")
        self._service_role_key = service_role_key
        self._bucket = bucket
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(45.0))
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def put(
        self, *, content: bytes, mime_type: str, object_key: str
    ) -> StoredMedia:
        _validate_media(content, mime_type)
        object_path = f"{self._bucket}/{object_key}"
        try:
            response = await self._client.post(
                f"{self._project_url}/storage/v1/object/{quote(object_path, safe='/')}",
                headers=self._headers(mime_type, upsert="false"),
                content=content,
            )
        except httpx.TimeoutException as exc:
            raise ProviderFailure("storage_timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderFailure("storage_network_error", retryable=True) from exc
        digest = hashlib.sha256(content).hexdigest()
        if _asset_already_exists(response):
            await self._verify_existing(object_path, digest)
        else:
            _raise_for_status(response, "storage")
        width, height = _image_dimensions(content, mime_type)
        return StoredMedia(
            provider="supabase",
            bucket=self._bucket,
            object_key=object_key,
            public_url=self._public_url(object_path),
            content_sha256=digest,
            mime_type=mime_type,
            width=width,
            height=height,
        )

    async def _verify_existing(self, object_path: str, expected_digest: str) -> None:
        try:
            response = await self._client.get(
                f"{self._project_url}/storage/v1/object/{quote(object_path, safe='/')}",
                headers=self._headers(),
            )
        except httpx.TimeoutException as exc:
            raise ProviderFailure("storage_verify_timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderFailure(
                "storage_verify_network_error", retryable=True
            ) from exc
        _raise_for_status(response, "storage_verify")
        actual_digest = hashlib.sha256(response.content).hexdigest()
        if not hmac.compare_digest(actual_digest, expected_digest):
            raise ProviderFailure("storage_existing_object_mismatch", retryable=False)

    async def delete(self, *, object_key: str) -> None:
        try:
            object_path = quote(f"{self._bucket}/{object_key}", safe="/")
            response = await self._client.request(
                "DELETE",
                f"{self._project_url}/storage/v1/object/{object_path}",
                headers=self._headers(),
            )
        except httpx.HTTPError:
            return
        if response.status_code not in {200, 204, 404}:
            return

    def _headers(
        self, mime_type: str | None = None, upsert: str | None = None
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._service_role_key}",
            "apikey": self._service_role_key,
        }
        if mime_type:
            headers["Content-Type"] = mime_type
        if upsert:
            headers["x-upsert"] = upsert
        return headers

    def _public_url(self, object_path: str) -> str:
        encoded_path = quote(object_path, safe="/")
        return f"{self._project_url}/storage/v1/object/public/{encoded_path}"


def _gemini_request(
    model: str,
    context: Mapping[str, Any],
    evidence: Sequence[EvidenceSource],
) -> dict[str, Any]:
    evidence_payload = [
        {
            "url": source.canonical_url,
            "title": source.title,
            "published_date": source.published_date,
        }
        for source in evidence
    ]
    instruction = (
        "Create exactly four practical wellbeing actions. Do not diagnose, prescribe, promise outcomes, "
        "or make medical claims. Cite only supplied evidence URLs. Return only the requested JSON schema."
    )
    return {
        "model": model,
        "store": False,
        "system_instruction": instruction,
        "input": json.dumps({"context": context, "evidence": evidence_payload}),
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": GEMINI_STRUCTURED_PLAN_SCHEMA,
        },
    }


def _gemini_plan_payload(response: Mapping[str, Any]) -> Mapping[str, Any]:
    status = response.get("status")
    if status != "completed":
        retryable = status in {"in_progress", "failed", "cancelled", "incomplete"}
        raise ProviderFailure("gemini_incomplete_response", retryable=retryable)
    text = response.get("output_text")
    if not isinstance(text, str):
        text = _last_model_text(response.get("steps"))
    if not isinstance(text, str):
        raise ProviderFailure("gemini_invalid_response", retryable=False)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderFailure("gemini_invalid_json", retryable=False) from exc
    if not isinstance(payload, Mapping):
        raise ProviderFailure("gemini_invalid_json", retryable=False)
    return payload


def _gemini_conversation_request(
    model: str, request: ConversationResponseRequest
) -> dict[str, Any]:
    """Keep provider input intentionally free of stable conversation/user identifiers."""

    return {
        "model": model,
        "store": False,
        "system_instruction": (
            "You are a wellbeing support assistant. "
            + request.instructions
            + " Do not diagnose, prescribe, state clinical approval, or mishandle emergencies."
        ),
        "input": json.dumps(
            {
                "thread_type": request.thread_type,
                "messages": [
                    {"role": message.role, "content": message.content}
                    for message in request.messages
                ],
            }
        ),
    }


def _gemini_conversation_text(response: Mapping[str, Any]) -> str:
    if response.get("status") != "completed":
        raise ProviderFailure(
            "gemini_incomplete_response",
            retryable=response.get("status")
            in {"in_progress", "failed", "cancelled", "incomplete"},
        )
    text = response.get("output_text")
    if not isinstance(text, str):
        text = _last_model_text(response.get("steps"))
    if not isinstance(text, str):
        raise ProviderFailure("gemini_invalid_response", retryable=False)
    return text


def _last_model_text(steps: object) -> str | None:
    if not isinstance(steps, list):
        return None
    for step in reversed(steps):
        if not isinstance(step, Mapping) or step.get("type") != "model_output":
            continue
        content = step.get("content")
        if not isinstance(content, list):
            continue
        texts = [
            item.get("text")
            for item in content
            if isinstance(item, Mapping)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ]
        if texts:
            return "".join(texts)
    return None


def _cloudflare_image_content(response: httpx.Response) -> tuple[bytes, str]:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type.startswith("image/"):
        return response.content, content_type
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise ProviderFailure(
            "cloudflare_image_invalid_response", retryable=False
        ) from exc
    image_value = _nested_image(payload)
    if not isinstance(image_value, str):
        raise ProviderFailure("cloudflare_image_invalid_response", retryable=False)
    try:
        return base64.b64decode(image_value, validate=True), "image/png"
    except ValueError as exc:
        raise ProviderFailure(
            "cloudflare_image_invalid_response", retryable=False
        ) from exc


def _nested_image(payload: object) -> object:
    if not isinstance(payload, Mapping):
        return None
    result = payload.get("result")
    return result.get("image") if isinstance(result, Mapping) else None


def _raise_for_status(response: httpx.Response, provider: str) -> None:
    if response.status_code < 400:
        return
    retryable = response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
    raise ProviderFailure(
        f"{provider}_http_{response.status_code}", retryable=retryable
    )


def _asset_already_exists(response: httpx.Response) -> bool:
    if response.status_code not in {400, 409}:
        return False
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, Mapping):
        return False
    detail = " ".join(
        str(payload.get(key, "")) for key in ("error", "message", "statusCode")
    )
    normalized = detail.casefold()
    return "already exists" in normalized or "duplicate" in normalized


def _validate_media(content: bytes, mime_type: str) -> None:
    if not content or len(content) > 10 * 1024 * 1024:
        raise ProviderFailure("storage_invalid_media", retryable=False)
    if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ProviderFailure("storage_unsupported_media_type", retryable=False)


def _image_dimensions(content: bytes, mime_type: str) -> tuple[int, int]:
    width, height, actual_mime_type = _inspect_image(content, "storage_invalid_image")
    if actual_mime_type != mime_type:
        raise ProviderFailure("storage_invalid_image", retryable=False)
    return width, height


def _inspect_image(content: bytes, error_code: str) -> tuple[int, int, str]:
    try:
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            actual_mime_type = Image.MIME.get(image.format)
            image.verify()
    except (OSError, SyntaxError) as exc:
        raise ProviderFailure(error_code, retryable=False) from exc
    if (
        width < 1
        or height < 1
        or width * height > 16_000_000
        or actual_mime_type not in {"image/jpeg", "image/png", "image/webp"}
    ):
        raise ProviderFailure(error_code, retryable=False)
    return width, height, actual_mime_type


def _integer(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0
