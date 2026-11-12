# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Choose a stream exactly: `--variant N` (its number in `rayohx info`, from 1), `--resolution 1280x720`, `--bandwidth` and `--variant-name`, in the commands and in `SelectionConfig`. A choice that matches nothing lists the available streams.
- `--no-space-check` (`StorageConfig(check_space=False)`) turns off the disk-space check, for drives that report free space wrongly.
- A Python guide, a generated Python reference and a guide for moving from `pym3u8downloader`.
- Linux packages (`.deb` and `.rpm`) are also built for ARM64.
- Damaged or hostile streams are refused with a clear error instead of crashing: playlists, MPEG-TS, MP4 and subtitles are tested against thousands of corrupted variations.
- A benchmark script (`scripts/benchmark.py`) to compare download speed and memory between changes.
- A Docker image for `linux/amd64` and `linux/arm64`: small, runs as a normal user, with folders for the output and for resuming. Publishing to GHCR stays off until `PUBLISH_GHCR` is set.
- The `rayohx` command: `rayohx info URL` shows a stream's qualities and tracks, and `rayohx download URL` saves it, with progress, `--json` output, settings from a file or `RAYOHX_` environment variables, and clear exit codes.
- `download(url, output)` downloads a stream to a file in one call, checks the result and reports progress, and a cancelled download returns a result instead of an error.
- MPEG-TS streams are saved as `.ts` and fragmented MP4 streams as `.mp4`; a name that does not match is refused instead of mislabelling the file.
- Streams whose audio is a separate track are joined into one MP4 automatically, in the language chosen, and MPEG-TS streams can be saved as MP4. The re-wrapping is built in and nothing is re-encoded.
- Subtitles are saved next to the video as `.vtt` files that line up with it.
- An installed FFmpeg is used as an optional fallback for codecs the built-in remuxer cannot copy. It is never required or shipped.
- `video_only` downloads just the video of a stream that has a separate audio track.
- Downloading a stream's segments in parallel into one file, with AES-128 decryption, a choice of storage strategies, accurate disk-space checks and no need for twice the output size.
- Interrupted or cancelled downloads can continue where they stopped, and leave only what resuming needs.
- Downloading playlists and media with retries, redirects, proxy, TLS settings, a speed limit and cancellation.
- `inspect(url)` shows a stream's variants and tracks without downloading it.
- Reading HLS playlists, including audio and subtitle tracks, encryption, byte ranges and fMP4.
- Choosing a stream by quality, resolution or bitrate limit, with audio and subtitle selection by language or name.
- Typed settings, results, progress events and clear errors for the downloader library.
- Secrets such as tokens, cookies and proxy passwords are hidden from logs and error messages.

### Changed

- Streams are numbered from 1 in `rayohx info` and its JSON.
- The command is now `rayohx` (`rayohx-cli` still works); the placeholder `hello` command is gone.
- The library is now imported as `rayohx`.
- A license check in CI blocks GPL and AGPL dependencies.
