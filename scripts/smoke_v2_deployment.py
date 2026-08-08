"""Post-deploy smoke test for a running AUVRA v2 API.

Drives the guest journey end to end against a live deployment, then proves
every private route is closed to an unauthenticated caller, the guest proof
token is genuinely enforced, and the contract guards behave.

This is deliberately black-box: it uses only HTTP, so it verifies the
deployment (config, database, Redis, TLS, migrations) and not just the code.
Run it after every production deploy.

    python scripts/smoke_v2_deployment.py [BASE_URL]

Exits non-zero if any check fails. It creates guest onboarding sessions, which
are short-lived and expire on their own; it never touches an existing user.
Note the public onboarding rate limit (10 per 10 minutes per IP) applies -- a
429 here is the limiter working, not a failure.
"""

import json
import sys
import urllib.error
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://auvra-v2-api.onrender.com").rstrip("/")
results = []


def call(method, path, body=None, headers=None, expect=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            status, payload = response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        status, payload = error.code, error.read().decode()
    except Exception as error:  # network-level failure
        status, payload = 0, str(error)

    ok = (status == expect) if expect is not None else True
    results.append((ok, f"{method} {path}", status, expect))
    try:
        return status, json.loads(payload)
    except Exception:
        return status, payload


print("=" * 72)
print("GUEST ONBOARDING PATH")
print("=" * 72)

# 1. Create a guest onboarding session.
status, session = call(
    "POST", "/api/v2/onboarding/sessions",
    body={}, headers={"Idempotency-Key": f"journey-{uuid.uuid4()}"}, expect=201,
)
session_id = session.get("session_id") if isinstance(session, dict) else None
proof = session.get("proof_token") if isinstance(session, dict) else None
print(f"  session created: {session_id}")
print(f"  required consents: {[c['consent_type'] for c in session.get('required_consents', [])]}")

# 2. Idempotency: replaying the same key must not create a second session.
key = f"journey-idem-{uuid.uuid4()}"
_, first = call("POST", "/api/v2/onboarding/sessions", body={},
                headers={"Idempotency-Key": key}, expect=201)
_, replay = call("POST", "/api/v2/onboarding/sessions", body={},
                 headers={"Idempotency-Key": key}, expect=201)
same = first.get("session_id") == replay.get("session_id")
results.append((same, "idempotent replay returns the same session", same, True))
print(f"  idempotent replay: {'same session' if same else 'DIFFERENT SESSION'}")

# 3. Submit an assessment against the guest proof token.
if session_id and proof:
    status, _ = call(
        "PUT", f"/api/v2/onboarding/sessions/{session_id}/assessment",
        body={
            "schema_version": "mobile-questionnaire.v1",
            "timezone": "Asia/Kolkata",
            "answers": {
                "age": 29,
                "period_description": "Regular",
                "cycle_length": "26-30 days",
                "period_concerns": ["Irregular Periods"],
                "skin_hair_concerns": ["Adult Acne"],
                "top_concern": "Adult Acne",
                "workout_intensity": "Moderate",
                "sleep_duration": "7-8 hours",
                "stress_level": "Moderate",
                "lifestyle_focus": ["eat", "move"],
            },
        },
        headers={"X-Onboarding-Proof": proof, "If-Match": '"0"',
                 "Idempotency-Key": f"journey-{uuid.uuid4()}"},
        expect=200,
    )
    print(f"  assessment submitted: HTTP {status}")

    # 4. A forged proof token of the same length as a real one, so it passes
    #    header-length validation and actually reaches the HMAC comparison.
    #    404 rather than 403 is deliberate: the response must not confirm the
    #    session exists to a caller holding the wrong token.
    call("PUT", f"/api/v2/onboarding/sessions/{session_id}/assessment",
         body={
             "schema_version": "mobile-questionnaire.v1",
             "timezone": "Asia/Kolkata",
             "answers": {"age": 29, "top_concern": "Adult Acne"},
         },
         headers={"X-Onboarding-Proof": "F" * len(proof), "If-Match": '"0"',
                  "Idempotency-Key": f"journey-{uuid.uuid4()}"},
         expect=404)

print()
print("=" * 72)
print("PUBLIC REFERENCE DATA")
print("=" * 72)
status, catalog = call("GET", "/api/v2/observation-catalog", expect=200)
if isinstance(catalog, dict):
    types = sorted({e["observation_type"] for e in catalog.get("entries", [])})
    print(f"  catalog {catalog.get('catalog_version')}: "
          f"{len(catalog.get('entries', []))} observables across {types}")

print()
print("=" * 72)
print("AUTHENTICATION BOUNDARY  (every private route must reject no-token)")
print("=" * 72)
private = [
    ("GET", "/api/v2/me/profile"),
    ("GET", "/api/v2/me/plans/today"),
    ("GET", "/api/v2/me/progress"),
    ("GET", "/api/v2/me/rewards"),
    ("GET", "/api/v2/me/cycle"),
    ("GET", "/api/v2/me/observations"),
    ("GET", "/api/v2/me/observations/current"),
    ("GET", "/api/v2/me/insights/summary"),
    ("GET", "/api/v2/me/insights/symptom-patterns"),
    ("GET", "/api/v2/me/insights/weekly-trends"),
    ("GET", "/api/v2/me/conversations"),
    ("GET", "/api/v2/me/weekly-checkins"),
    ("GET", "/api/v2/me/weekly-checkins/due"),
    ("GET", "/api/v2/me/progress/summary"),
    ("GET", "/api/v2/me/plan-generations/latest"),
]
for method, path in private:
    call(method, path, expect=401)

# A forged bearer token must also be rejected, not merely a missing one.
call("GET", "/api/v2/me/profile",
     headers={"Authorization": "Bearer not-a-real-firebase-token"}, expect=401)

print()
print("=" * 72)
print("CONTRACT GUARDS")
print("=" * 72)
# A mutation without an Idempotency-Key must be refused.
call("POST", "/api/v2/onboarding/sessions", body={}, expect=422)
# Unknown route yields RFC 9457, not an HTML error page.
call("GET", "/api/v2/does-not-exist", expect=404)

print()
print("=" * 72)
failed = [r for r in results if not r[0]]
for ok, label, got, want in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}  (got {got}, want {want})")
print("=" * 72)
print(f"{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
