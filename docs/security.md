# Security

How the API and the UI window protect requests. To report a vulnerability, see [SECURITY.md](../SECURITY.md).

## Under the UI window

Cornea's `SecurityMiddleware` guards every mounted route, so the API adds no token of its own:

- the `Host` must be on the allow-list (the bound `127.0.0.1:<port>` only)
- state-changing requests (POST, PUT, PATCH, DELETE) must carry a matching `Origin`
- a one-time start-up token is exchanged for an `HttpOnly`, `SameSite=Strict` session cookie
- responses carry a strict content security policy and other protective headers

`example_ui` therefore mounts the routes with API-key checks switched off. Never expose these routes any other way.

## Standalone API

When run alone (`python -m example_api`), Cornea is absent, so the API protects itself:

- **API key:** protected routes need an `X-API-Key` header matching `EXAMPLE_API_API_KEYS`. The app will not start without a key unless `EXAMPLE_API_AUTH_MODE=none`, which is for local development only. Keys are compared in constant time and never logged.
- **Headers:** `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a restrictive CSP, `Cache-Control: no-store` on `/api/` and HSTS over HTTPS.
- **Body limit:** requests declaring more than `EXAMPLE_API_MAX_BODY_BYTES` get `413`. Also set a limit at your reverse proxy, since chunked bodies have no declared size.
- **Docs hidden:** `/docs`, `/redoc` and `/openapi.json` are off unless `EXAMPLE_API_DOCS_ENABLED=true`.
- **Errors:** a `500` never shows details; they go to the log with the request ID.
- **Not included:** HTTPS, rate limiting, JWT and multi-user authentication, a database. Add them, or put the API behind a proxy, before exposing it publicly.

## Supply chain

Dependabot covers Python, npm and Actions. `audit.yml` checks locked dependencies weekly, dependency review blocks vulnerable additions, and the lock files are committed.
