# Security

How the API and the UI window protect requests. To report a vulnerability, see [SECURITY.md](../SECURITY.md).

## Under the UI window

Cornea's `SecurityMiddleware` guards every mounted route, so the API adds no token of its own:

- the `Host` must be on the allow-list (the bound `127.0.0.1:<port>` only)
- state-changing requests (POST, PUT, PATCH, DELETE) must carry a matching `Origin`
- a one-time start-up token is exchanged for an `HttpOnly`, `SameSite=Strict` session cookie
- responses carry a strict content security policy and other protective headers

`rayohx_ui` therefore mounts the routes with API-key checks switched off. Never expose these routes any other way.

## Standalone API

When run alone (`python -m rayohx_api`), Cornea is absent, so the API protects itself:

- **API key:** protected routes need an `X-API-Key` header matching `RAYOHX_API_API_KEYS`. The app will not start without a key unless `RAYOHX_API_AUTH_MODE=none`, which is for local development only. Keys are compared in constant time and never logged.
- **Headers:** `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a restrictive CSP, `Cache-Control: no-store` on `/api/` and HSTS over HTTPS.
- **Body limit:** requests declaring more than `RAYOHX_API_MAX_BODY_BYTES` get `413`. Also set a limit at your reverse proxy, since chunked bodies have no declared size.
- **Docs hidden:** `/docs`, `/redoc` and `/openapi.json` are off unless `RAYOHX_API_DOCS_ENABLED=true`.
- **Errors:** a `500` never shows details; they go to the log with the request ID.
- **Not included:** HTTPS, rate limiting, JWT and multi-user authentication, a database. Add them, or put the API behind a proxy, before exposing it publicly.

## Download engine

- **Redaction:** URLs (user info and values of keys such as `token`, `sig`, `key`), `Authorization`, `Cookie` and proxy credentials are hidden in log records and error messages by `rayohx.redaction`. Every exception message and every handler added by `configure_logging` goes through it, including formatted tracebacks.
- **Settings:** `repr` and `as_dict()` of the settings leave out header values, cookie values and proxy passwords. Resume state must be built from `as_dict()`, never from the raw objects.
- **Input checks:** download addresses must be absolute `http` or `https` URLs without control characters. Header names must be valid tokens, and header and cookie values cannot hold line breaks, which stops header injection. Proxies must be `http` or `https`.
- **Credentials per host:** secret headers and configured cookies go only to the starting host and the hosts listed in `credential_hosts`, including across redirects. Redirect targets must be `http` or `https`.
- **Network defaults:** TLS certificates are verified, and a warning is logged whenever verification is turned off. Proxy settings and environment variables such as `HTTPS_PROXY` are not read implicitly. Response sizes are limited (32 MiB for a playlist, 512 MiB for a single media request by default).
- **Address guard:** every request address, including redirect targets, can be vetted by a `url_guard` function, which keeps server-side request forgery protection possible for future server use.
- **Working folder:** its name is a hash of the address and output name, never user input, so it cannot point elsewhere. It is created readable by the current user only, where the system allows, and locked while in use. Resume state holds a fingerprint, counters and a redacted address, never headers, cookies or keys. Keys are kept in memory only.
- **File names:** names made from an address contain only letters, digits, dots, dashes and underscores, never start with a dot, never contain a path separator and are never Windows device names, so a hostile address cannot write outside the chosen folder. A name you give is used as given, but its extension must match the content.
- **No DRM claim:** only plain AES-128 is decrypted. SAMPLE-AES and DRM key formats are refused before downloading.
- **Files:** an existing output file is never replaced unless `overwrite` is on. Output is written to a `.part` file and renamed only when complete.

## Supply chain

Dependabot covers Python, npm and Actions. `audit.yml` checks locked dependencies weekly, dependency review blocks vulnerable additions, and the lock files are committed.
