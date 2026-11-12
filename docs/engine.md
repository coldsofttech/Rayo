# Engine design

The HLS engine lives in the `rayohx` package. The command line, the API and the desktop window all call it, so download logic exists in one place. The engine never prints, exits or reads global state: it takes typed configuration and reports through events.

## Responsibilities

```mermaid
flowchart TD
    F[Library API / CLI / API / UI] --> C[Download controller]
    C --> P[Playlist parser]
    C --> S[Stream selector]
    C --> D[Download manager]
    P --> H[HTTP layer]
    D --> H
    D --> Q[Segment scheduler]
    Q --> B[Bounded buffer]
    Q --> W[Workspace and storage]
    B --> O[Output and muxer]
    W --> O
    O --> R[Result]
```

| Part | Job |
| --- | --- |
| Playlist parser | Tokenizing HLS parser for master and media playlists, with RFC 3986 URL resolution |
| Stream selector | Deterministic choice of video, audio and subtitle tracks from user constraints |
| HTTP layer | The only place that makes requests: pooling, retries, proxy, TLS, headers, cookies, redaction |
| Segment scheduler | Bounded worker pool, per-segment state, ordered delivery, cooperative cancellation |
| Workspace and storage | Safe workspace, storage strategies, disk checks, metrics, resume state |
| Output and muxer | Container detection and finalization (see below) |
| Events | Typed events and state changes that every front end consumes |

## Package layout

Modules are added phase by phase under `src/rayohx/`. The first group exists; the second is still to come.

| Module | Contents |
| --- | --- |
| `enums` | Download and segment states, event types, container, encryption, storage and output choices |
| `models` | Frozen dataclasses for playlists, tracks, segments, requests, progress and results |
| `config` | `DownloadConfig`, `NetworkConfig`, `ProxyConfig`, `OutputConfig`, `StorageConfig`, `SelectionConfig` |
| `errors` | The `RayoHXError` hierarchy |
| `playlist` | The HLS parser for master and media playlists |
| `selection` | Choosing the variant, audio and subtitle tracks |
| `inspection` | `inspect_playlist`, which looks at a stream without downloading it |
| `http_client` | `HttpClient`: the only code that makes network requests |
| `ratelimit` | A speed limit shared by all workers |
| `crypto` | AES-128 decryption and key retrieval |
| `fetcher` | Downloads and decrypts one segment |
| `storage` | Size estimates, disk-space checks, metrics and the segment stores |
| `workspace` | The private working folder, its lock and cleanup |
| `state` | Resume state, saved atomically |
| `sinks` | `FileSink` and `StreamSink`: where ordered segments go |
| `progress` | Speed, ETA and progress snapshots |
| `pipeline` | Parallel fetching with ordered writing |
| `media_download` | `MediaDownload`: one media playlist from start to finish |
| `cancellation` | `CancellationToken` for cooperative cancellation |
| `events` | The download state machine and the `EventBus` |
| `redaction` | Hides secrets in URLs, headers and text |
| `logs` | Standard `logging` setup with a redaction filter |

Still to come: the output and muxing code (`output`) and the controller that joins everything behind `download()`.

## Conventions

- **Immutable and validated.** Models and settings are frozen dataclasses. A settings object checks itself when it is created and raises `ConfigurationError`. Collections are stored as tuples or read-only mappings.
- **Secrets stay out.** Headers, cookies and proxy passwords are left out of `repr`, and `as_dict()` hides them, so the result is safe for logs and resume state. A `DownloadRequest` or `ProxyConfig` shows its address with credentials removed.
- **Events, not prints.** The engine reports through `DownloadEvent` objects delivered by an `EventBus`. A listener that raises is logged and never stops the download.
- **Explicit states.** `DownloadStateMachine` allows only the moves in `TRANSITIONS`, safely across threads. `COMPLETED`, `FAILED` and `CANCELLED` are final. A cancelled download ends as `CANCELLED`, not as a failure.
- **Errors say what to do.** Every `RayoHXError` has a redacted `message` and an optional `hint`. `describe()` joins them.
- **Logging.** Loggers live under `rayohx` and the library stays silent until an application calls `configure_logging`. Logs go to stderr, never stdout, and every record is redacted and tagged with the download id.

The greeting example (`core.py`, `greetings.py`) is temporary. It stays only until the API and window are moved onto the engine, then it is deleted.

## Output without FFmpeg

RayoHX does its own container handling and never bundles FFmpeg.

1. MPEG-TS segments are joined into a `.ts` file, and fMP4 segments (init segment plus fragments) into a fragmented `.mp4`. Neither step decodes anything.
2. A native muxer turns MPEG-TS into fragmented MP4 for H.264 and HEVC video with AAC, AC-3, E-AC-3 or MP3 audio. It is written from the public specifications.
3. WebVTT subtitles are written as a sidecar file.
4. For anything else, RayoHX uses an FFmpeg already installed on the machine, when found on `PATH`. It is a prerequisite for those cases only, and it runs as a separate program, never linked or shipped.

RayoHX remuxes and never transcodes. When no route exists, it raises `OutputFormatError` and says which stream or codec is the problem.

## Licensing

Every runtime dependency must be permissively licensed (MIT, BSD, Apache-2.0 and similar). CI runs `pip-licenses` and fails on any GPL-family license. PyInstaller, which is GPL-2.0 with a bootloader exception that permits bundling commercial applications, is only an installer-build tool and is not a runtime dependency. Do not copy code from GPL, LGPL or AGPL projects such as FFmpeg, Bento4 or GPAC; implement from the specifications instead.

## Playlists and selection

**Parsing.** `parse_playlist(text, uri)` reads a master or media playlist and returns the typed models, with every address made absolute using RFC 3986 rules against the playlist's own (final) address.

- A playlist is recognised by its `#EXTM3U` header, never by its file name or address.
- Tags the parser does not know are skipped, so newer playlists keep working.
- Errors name the line. A web page or other non-playlist gets a hint to check the address, headers and cookies.
- Playlist variables (`EXT-X-DEFINE`) and I-frame-only playlists are refused with `UnsupportedPlaylistError`, because ignoring them would produce wrong addresses or wrong video.
- Segments and playlists must be `http` or `https`. Key addresses may use another scheme so that a SAMPLE-AES or DRM playlist can still be inspected. The key is marked unsupported and the download refuses it later.
- A key without an `IV` keeps `iv=None`. The decrypting phase derives it from the segment's `sequence_number`, as the HLS specification says.
- `EXT-X-BYTERANGE` ranges without an offset are resolved against the previous range of the same file.
- The container (MPEG-TS, fMP4, WebVTT) is guessed from the playlist. When it is unclear it stays `UNKNOWN` and the first bytes of a segment settle it.

**Inspecting.** `inspect_playlist(url, fetcher)` downloads no media. The fetcher is any object with `fetch_text(url)`, which returns the text and its final address after redirects. `HttpClient` is the real one. `inspect(url, network_config)` wires them together.

**Selecting.** `select_stream(master, config)` is a pure function, so the same playlist and settings always give the same answer. If a wish cannot be met, the closest track is used and a note says why.

| Setting | Behavior |
| --- | --- |
| `quality="best"` | Largest picture, then bandwidth, average bandwidth, frame rate and codec support. Equal streams: the first listed. |
| `quality="worst"` | The smallest picture, then the lowest bandwidth. |
| `quality="1080p"` | The best stream at or below that size. If every stream is larger, the smallest one. |
| `quality="auto"` | Like `1080p`: sharp without the cost of the largest stream. |
| `max_resolution`, `max_bitrate` | Limits applied before the quality choice. If nothing fits, the smallest stream is used. A stream that does not state its size is never ruled out by a size limit. |
| `audio_languages`, `audio_names` | One track per entry, in the order given. If none is found, the default audio track is used. `en` matches `en-US`, but `en-US` does not match `en-GB`. |
| `subtitle_languages`, `subtitle_names` | Subtitles are off by default. A missing language selects nothing and adds a note. |

Picture size is the shorter side, so a 720x1280 portrait stream counts as 720p. Audio-only streams are skipped when video streams exist. Forced tracks are chosen only when nothing else matches or when asked for by name.

## Network layer

`HttpClient` (built on `httpx`) is the only place that makes requests. It is safe to share between worker threads and reuses connections.

| Area | Behavior |
| --- | --- |
| Timeouts | `connect_timeout` and `read_timeout` apply to each step. `total_timeout` caps one call, retries included. |
| Retries | `retries` more attempts after a connection failure, timeout, HTTP 408, 425, 429 or 5xx, a web page served as media, an empty body, or a body cut short. The wait grows as `backoff_factor * 2^n` with a little random spread. `Retry-After` is obeyed up to 60 seconds. |
| Not retried | Other 4xx answers, a refused certificate, a server that ignores a byte range, a response over the size limit, and an expired overall limit. |
| Redirects | Followed one hop at a time, up to `max_redirects`. Only `http` and `https` targets are accepted. |
| Validation | Media must not be a web page (checked by content type and by content), must not be empty, must match `Content-Length`, and a ranged request must get exactly the range it asked for. |
| Speed limit | `rate_limit` is shared by all workers. |
| Cancellation | A `CancellationToken` stops waiting, retrying and reading between chunks. |
| TLS | Verified by default. `verify_tls` can be off or a CA file. |
| Proxy | HTTP and HTTPS proxies, with credentials sent as proxy authentication. Environment variables such as `HTTPS_PROXY` are not read; settings come only from the configuration. |

**Credentials stay with their host.** Secret headers (`Authorization`, `Cookie`, API keys, anything with token or secret in its name) and the configured cookies are sent to the host the download started from and to `credential_hosts`. A redirect or a segment on another host gets `Referer`, `Origin`, `User-Agent` and other plain headers, but none of the secrets. Cookies the server sets are not stored.

**Address guard.** `HttpClient(url_guard=...)` is called with every address, redirect targets included, and can refuse it. A server deployment can use it to block private networks without changing the engine.

Compressed responses are accepted for playlists. Media is requested uncompressed so sizes can be checked.

## Downloading segments

`MediaDownload(playlist, client=..., config=..., sink=...).run()` downloads one media playlist. Workers fetch and decrypt segments in parallel, and the calling thread writes them to the sink in playlist order, so a sink never needs locking. Segments that finish early wait in a *store*. New work starts only while the strategy allows it, so waiting data stays bounded. A segment leaves the store the moment it is written, so working storage never grows to the size of the stream.

| Strategy | Where finished segments wait | Notes |
| --- | --- | --- |
| `AUTO` (default) | | `DIRECT` for a resumable file, `STREAMING` for a pipe. |
| `DIRECT` | Memory, up to twice the worker count past the segment being written | Writes the output file directly. Needs a resumable file output. |
| `STREAMING` | The same | For pipes and other outputs that cannot resume. Uses no disk. |
| `BUFFERED` | Memory, up to `buffer_bytes` | Absorbs a slow segment or a slow consumer. |
| `TEMPORARY` | Files in the working folder | Every finished segment survives a crash. `max_temp_bytes` bounds it. |
| `HYBRID` | Memory up to `buffer_bytes`, then files, up to `max_temp_bytes` (1 GiB if unset) | Absorbs bursts without growing without limit. |

The next segment to be written is always allowed to start, which rules out a deadlock.

**Disk space.** The check depends on the strategy and never assumes "twice the output". `DIRECT` needs the output size, `STREAMING` and `BUFFERED` to a pipe need no disk, and `TEMPORARY` and `HYBRID` add a bounded working amount. If the output and the working folder are on one disk, their needs are added. The size is estimated from the stream's bitrate (the average bitrate when given) and duration, with 5% added. It is refined from the segments actually downloaded and checked again after three segments, without asking the server for the size of every segment. A full disk during a write becomes `InsufficientStorageError`.

**Output.** `FileSink` writes `<name>.part` beside the output and renames it only when the download is complete. An existing output is refused before anything is downloaded, unless overwriting was allowed. `StreamSink` passes bytes to any function.

**Resume.** The working folder (`.rayohx-<hash>` beside the output, or inside `workspace_dir`) holds a small state file with a fingerprint of the playlist (ignoring query strings), the next segment and the bytes written. It is saved atomically every 16 segments or 2 seconds, and when a download stops. A later run with the same address and output continues after the last durable segment, reuses staged segments, and discards state that does not match the stream. Turning `resume` off ignores and removes it. A lock prevents two downloads from using one folder.

**Cleanup.** After a success nothing is left but the output. After a failure or cancellation only what resuming needs remains: the state, the partial file, and staged segments. With `resume` off or `keep_state_on_failure` off, nothing is left.

**Encryption.** Plain AES-128 is decrypted, with the IV from the playlist or the segment's sequence number. Keys come through the HTTP layer and are cached. Initialization sections are treated as unencrypted. SAMPLE-AES and DRM key formats are refused before anything is downloaded; encrypted segments are not DRM support. Live streams (no `#EXT-X-ENDLIST`) are refused.

**Events.** `SEGMENT_STARTED`, `SEGMENT_COMPLETED`, `SEGMENT_FAILED` and `DOWNLOAD_PROGRESS` (at most four a second) go to the `EventBus`. Progress carries bytes downloaded and written, estimated total, current and average speed, ETA, active workers, and storage figures including peak temporary use.
