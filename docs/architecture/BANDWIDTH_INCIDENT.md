# Bandwidth warning — what happened and what was done

Render warned that the workspace had passed 70% of the 5 GB monthly bandwidth
included in the Hobby plan. This is the investigation and the fix.

## What was actually using it

Per-service figures for the month, from Render's metrics API:

| Service | Bandwidth | Note |
|---|---:|---|
| `auvra-v2-worker` | **3.76 GB** | the cause |
| `Auvra-Backend` (legacy) | 346 MB | served the app until it was retired |
| `auvra-v2-api` | 294 MB | normal |

The worker was burning a steady **15.8 MB/hour** — roughly 380 MB/day, or
about **11.4 GB/month**, more than twice the entire plan allowance, on its own.

## Why

The worker runs four job loops — plan generation, conversation replies,
account export and account deletion. Each polled **once per second, forever**,
and each poll ran two queries against Supabase **across the public internet**.

With an empty queue that is roughly **691,000 queries a day** asking whether
there was anything to do. Plan generation is triggered by a user action, not a
schedule, so the queue is idle almost all of the time. Nothing was wrong with
the app; the worker was simply asking a question, very fast, forever.

A second, smaller contributor: both services were configured with Render's
**public** Redis endpoint rather than its private-network address, so every
rate-limit check also left the network and was billed as egress.

## The fix

1. **Idle backoff.** The poll interval now doubles while the queue is idle, up
   to a 30-second ceiling, and resets to the base interval the instant a job is
   claimed. A busy queue still drains at full speed; only an idle worker slows
   down. Idle polling drops roughly thirtyfold.

2. **Private-network Redis.** Both services now use the internal address.
   That traffic is not billed, is faster, and cannot be disrupted by public
   internet conditions. The configuration validator previously demanded TLS
   unconditionally, which forced every request onto the public internet; it now
   requires TLS only for a public hostname and still rejects plaintext to one.

3. **The legacy service is suspended**, so its share stops accruing.

Projected worker usage after the change is on the order of a few hundred
megabytes a month rather than 11 GB. The figure should be confirmed against
Render's metrics once a full day has elapsed, since the metrics API lags by
about an hour.

## One thing worth flagging

While investigating, commit `113b928` — *"temporarily bypass rate limit
fail-close to unblock users during Redis outage"* — was found in the history.
It made the API skip rate limiting entirely whenever Redis was unreachable.

It is an understandable emergency response, and its own message called it
temporary, but it removed the only limit on the unauthenticated onboarding
endpoint and, more importantly, on the paths that spend money: plan generation
and conversation messages both call LLM and image providers. A Redis outage
would have turned into an unbounded provider bill — a poor trade in the same
week as a billing warning.

Fail-closed behaviour is restored. The transient Redis blip that motivated the
bypass is fixed at its source instead: the client now retries connection
failures with bounded backoff, and the private-network address removes the
public-internet dependency that caused the blip.

## Keeping an eye on it

```bash
# Per-service bandwidth for the current month
curl -s -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/metrics/bandwidth?resource=<service-id>&startTime=<iso8601>"
```

If usage climbs again, check the worker's hourly rate first — a steady figure
with an empty queue means a polling loop, not real traffic.
