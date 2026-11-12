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
- **FFmpeg:** if used, it is started without a shell, with a fixed argument list built from paths RayoHX chose itself, no standard input, and only on files already downloaded. It is only asked to copy streams. Its output is shown with secrets removed, and it is stopped at once if the download is cancelled. FFmpeg is found only on the `PATH` or at a path the user configures.
- **Temporary files:** separate audio tracks and FFmpeg inputs are saved as hidden files beside the output (never in a shared temporary folder) and removed on success, failure and cancellation.
- **Untrusted media:** the demuxer and codec parsers read untrusted data, so every length and count is checked against the data actually present, and malformed headers raise a clear error instead of reading past the end. Buffered data waiting for another track is capped at 64 MiB.
- **No DRM claim:** only plain AES-128 is decrypted. SAMPLE-AES and DRM key formats are refused before downloading.
- **Files:** an existing output file is never replaced unless `overwrite` is on. Output is written to a `.part` file and renamed only when complete.
- **Command line:** `--header`, `--cookie` and the other options that take values accept `@file`, so secrets need not appear in shell history or the process list. Addresses, log lines (including `--log-file`) and error output are redacted, and a traceback is shown only with `--verbose` and never with local variables. A settings file with an unknown key is refused. `--insecure` turns certificate checks off and logs a warning each time.
- **Docker image:** it runs as an unprivileged user (uid `10001`) with no login shell, holds only the two packages and their dependencies (no compilers, no pip, no FFmpeg), and publishes with provenance. Secrets are passed as mounted files, never baked into the image.
- **Damaged and hostile media:** the playlist parser, the transport stream and MP4 readers, the codec parsers and the subtitle merger are fuzzed with repeatable damage (flipped, cut, removed and inserted bytes, and random data) in `tests/core/test_hardening.py`. Damaged input ends in one of the engine's own errors, never an unhandled exception or an endless loop; implausible timestamps are ignored instead of breaking the output file. Run it longer with `RAYOHX_FUZZ_SEEDS=1500`.
- **End-to-end checks:** `tests/core/integration/test_security_end_to_end.py` shows over real sockets that credentials are not sent to another host after a redirect, redirects to `file:` and other schemes are refused, redirect loops end, segment addresses with `../` cannot write outside the output folder, an existing file survives a failed download, and the command's output and log never contain a token, header or cookie.

## Supply chain

Dependabot covers Python, npm and Actions. `audit.yml` checks locked dependencies weekly, dependency review blocks vulnerable additions, and the lock files are committed.
