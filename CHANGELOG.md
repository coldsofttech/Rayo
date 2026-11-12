# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Reading HLS playlists, including audio and subtitle tracks, encryption, byte ranges and fMP4.
- Choosing a stream by quality, resolution or bitrate limit, with audio and subtitle selection by language or name.
- Typed settings, results, progress events and clear errors for the downloader library.
- Secrets such as tokens, cookies and proxy passwords are hidden from logs and error messages.

### Changed

- The library is now imported as `rayohx`.
- A license check in CI blocks GPL and AGPL dependencies.
