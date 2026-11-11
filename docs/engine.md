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

New modules are added phase by phase under `src/rayohx/`:

| Module | Contents |
| --- | --- |
| `models` | Frozen dataclasses for playlists, tracks, segments, requests, progress and results |
| `config` | `DownloadConfig`, `NetworkConfig`, `ProxyConfig`, `OutputConfig`, `StorageConfig` |
| `errors` | The `RayoHXError` hierarchy, with credentials redacted |
| `events` | `DownloadState` and `DownloadEvent` |
| `logs` | Standard `logging` setup and the redaction filter |
| `playlist`, `selection`, `http`, `storage`, `scheduler`, `crypto`, `output` | The parts above |

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
