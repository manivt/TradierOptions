# 04 - Tradier API client

[Index](README.md) | Prev: [03 - Configuration](03-configuration.md) | Next: [05 - Market clock](05-market-clock.md)

Code: `tradier_collector/tradier_client.py` | Tests: `tests/test_tradier_client.py` (25 tests)

## Public surface

```python
TradierClient(api_token, *, base_url, timeout, max_attempts, session, sleep)
  .get_quote(symbol, greeks=True)      -> dict          (raises if absent)
  .get_quotes(symbols, greeks=True)    -> list[dict]    (always a list)
  .get_expirations(symbol)             -> list[str]     (always a list)
  .get_chain(symbol, expiration, ...)  -> list[dict]    (always a list)
batched(items, size) -> Iterable[list[str]]
```

`session` and `sleep` are injection points: the tests pass a `FakeSession` and
collect sleep durations instead of waiting, which is why the retry tests run
instantly and offline.

## Endpoints used

| Method | Endpoint |
| --- | --- |
| `get_quote` / `get_quotes` | `GET /markets/quotes` |
| `get_expirations` | `GET /markets/options/expirations` |
| `get_chain` | `GET /markets/options/chains` |

Headers set once on the persistent session: `Authorization: Bearer <token>`,
`Accept: application/json`, `Accept-Encoding: gzip`, a project user-agent.
Connection reuse matters on an e2-micro polling every 60 seconds.

## Response-shape normalisation (the subtle part)

Tradier wraps payloads inconsistently.  `quotes.quote`, `options.option` and
`expirations.date` can each be **null**, a **single object**, or a **list**.
`_as_list` collapses all three; `get_expirations` handles the scalar-string case
separately.  Every public method therefore returns a predictable type, so no
caller in the codebase writes `isinstance` checks against vendor payloads.

`quotes.unmatched_symbols`, when present, is logged as a warning - it is how a
delisted or mistyped symbol shows up.

## Retry policy

Implemented as an explicit loop, not a decorator library, because we must read
`Retry-After` and the rate-limit headers and must stay bounded (I10).

* Retried: `requests.exceptions.RequestException`, HTTP 429/500/502/503/504.
* Never retried: 400, 401, 403, 404, 422 - these do not become healthy by waiting.
* Backoff: 1s, 2s, 4s (`2 ** (attempt - 1)`).
* `Retry-After` overrides the backoff **only** when it is between 0 and 120
  seconds; an absurd value (for example 86400) is ignored so a bad header cannot
  stall a whole session.
* On 429, `X-Ratelimit-Allowed / Used / Available / Expiry` are logged.

## Error contract

`TradierAPIError(message, endpoint=..., status=..., body=...)` carries the
endpoint, HTTP status and a **sanitized** body.  `sanitize_body` redacts
bearer-token and `api_key`/`access_token` patterns, collapses newlines and
truncates at 500 characters.  Invalid JSON and non-object JSON both raise the
same error type, so callers have exactly one exception to handle.

## Why `tenacity` was removed

It is in the "preferred dependencies" list in the original brief, but expressing
"inspect the response headers, clamp `Retry-After`, log rate-limit state, stop at
3" through `tenacity` is less readable than 40 lines of loop.  Keeping an unused
dependency on an e2-micro is also waste.  Decision D5 in
[16 - Decision log](16-decision-log.md).

Related: [09 - Collection cycle](09-collection-cycle.md), [18 - Failure modes](18-failure-modes.md)
