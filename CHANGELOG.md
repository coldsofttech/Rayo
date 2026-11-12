# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `download(url, output)` downloads a stream to a file in one call, checks the result and reports progress, and a cancelled download returns a result instead of an error.
- MPEG-TS streams are saved as `.ts` and fragmented MP4 streams as `.mp4`; a name that does not match is refused instead of mislabelling the file.
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

- The library is now imported as `rayohx`.
- A license check in CI blocks GPL and AGPL dependencies.
