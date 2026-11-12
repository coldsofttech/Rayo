# Moving from pym3u8downloader

RayoHX replaces `pym3u8downloader` (and the M3U8 Downloader window, `pym3u8downloaderui`). It is a new package with a different, much larger feature set, so there is no compatibility layer: update your calls as shown below.

```bash
pip uninstall pym3u8downloader
pip install RayoHX
```

## What changed

- **Output is real.** `pym3u8downloader` always wrote a file called `.mp4` that was really the segments joined together. RayoHX saves MPEG-TS as `.ts` and fragmented MP4 as `.mp4`, can turn MPEG-TS into a genuine `.mp4` (`--format mp4`) without FFmpeg, and joins separate audio tracks. A name that does not match the content is refused rather than mislabelled.
- **One entry point** for playlists and master playlists. There is no `download_master_playlist`; the same call detects the kind and chooses a stream.
- **No internet pre-check, no temporary folder in the system temp.** Errors come from the actual request, with a hint. Working files live beside the output (or in `--workspace`) and are cleaned up, and an interrupted download resumes.
- **Python 3.11 or newer**, and `httpx` instead of `requests`.

## Library

| pym3u8downloader | RayoHX |
| --- | --- |
| `M3U8Downloader(input_file_path=url, output_file_path="out").download_playlist()` | `download(url, "out.ts")` |
| `download_master_playlist(name=..., bandwidth=..., resolution="1280x720")` | `download(url, "out.mp4", config=DownloadConfig(selection=SelectionConfig(variant_name=..., bandwidth=..., resolution="1280x720")))` |
| `download_master_playlist()` returning the variants as JSON (`M3U8DownloaderWarning`) | `playlist_to_dict(inspect(url))`, or `rayohx info URL --json` |
| `download_playlist(merge=False)` | `download(url, "folder", format="segments")`; segments are named `segment-00001.ts` and are written into the folder you name |
| `max_threads=10` | `concurrency=10` (1 to 64, default 8) |
| `verify_ssl=False` | `NetworkConfig(verify_tls=False)` (a warning is logged), or `ca_bundle=` for a private CA |
| `skip_space_check=True` | `StorageConfig(check_space=False)` |
| `debug=True, debug_file_path="debug.log"` | `configure_logging("DEBUG", log_file="debug.log")`; secrets are removed |
| `is_download_complete` | `result.success` |
| `M3U8DownloaderError` | `RayoHXError` and its subclasses (for example `SegmentDownloadError`, `OutputExistsError`) |

The old exact match on `name`, `bandwidth` and `resolution` still exists (`variant_name`, `bandwidth`, `resolution`), and you can also choose a stream by its number or by quality (`"720p"`, `"best"`). See the [Python guide](python.md).

## Command line

`pym3u8downloader` had no command line. RayoHX has `rayohx info URL` and `rayohx download URL`; see the [command line guide](cli.md). The equivalents of the old options:

| Old | New |
| --- | --- |
| `name=`, `bandwidth=`, `resolution=` | `--variant-name`, `--bandwidth`, `--resolution` (or `--variant N`) |
| `max_threads` | `--concurrency` / `-j` |
| `verify_ssl=False` | `--insecure` |
| `skip_space_check` | `--no-space-check` |
| `merge=False` | `--format segments` |
| `debug`, `debug_file_path` | `--verbose`, `--log-file` |

## The window

The old window offered a URL, an output folder, a "skip SSL" checkbox and a variant picker. The RayoHX window is planned on the same engine (the API in `rayohx_api` and the window in `rayohx_ui` are in the repository); until it is released, use the command line.

## Things the old project could not do

Encrypted (AES-128) streams, audio and subtitle tracks, fragmented MP4, retries with back-off, a speed limit, a proxy, headers and cookies, resume, cancellation, progress events, `--json` output, Docker, and FFmpeg-free MP4 output.
