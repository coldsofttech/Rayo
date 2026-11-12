# Python guide

RayoHX is a Python library first: the command line is a thin layer over it. Everything below is importable from `rayohx`; the full list of classes, functions and settings is in the [Python reference](python-reference.md) (generated from the code).

```bash
pip install RayoHX      # Python 3.11 or newer
```

## Download

```python
from rayohx import download

result = download("https://example.com/master.m3u8", "video.mp4", quality="720p")
if result.success:
    print(result.output.path, result.output.size_bytes)
else:
    print("not finished:", result.state, result.error)
```

`download` returns a `DownloadResult` for completed, failed (when the failure is part of the result) and cancelled downloads, and raises a `RayoHXError` for problems it cannot recover from, such as a bad address or an existing output file. Every error has a readable message and often a `hint`.

## Look first, then choose

```python
from rayohx import SelectionConfig, inspect, select_stream

master = inspect("https://example.com/master.m3u8")      # nothing is downloaded
for number, variant in enumerate(master.variants, start=1):
    print(number, variant.resolution, variant.bandwidth, variant.name)

choice = select_stream(master, SelectionConfig(resolution="1280x720"))
print(choice.variant.uri, [track.name for track in choice.audio_tracks], choice.notes)
```

A stream can be chosen by `quality` (`"best"`, `"worst"`, `"auto"`, or a height such as `"720p"`), limited with `max_resolution` and `max_bitrate`, or chosen exactly with `variant` (its number in the listing, starting at 1), `resolution` (`"1280x720"`), `bandwidth` (bits per second, matching the bandwidth or the average bandwidth) and `variant_name`. `variant` cannot be combined with the others. Audio and subtitles are chosen with `audio_languages`, `audio_names`, `subtitle_languages` and `subtitle_names`.

## Settings

Settings are immutable dataclasses, grouped by area:

```python
from pathlib import Path
from rayohx import (
    DownloadConfig, NetworkConfig, OutputConfig, OutputFormat, SelectionConfig, StorageConfig,
)

config = DownloadConfig(
    concurrency=8,
    network=NetworkConfig(headers={"Referer": "https://example.com/"}, retries=5),
    selection=SelectionConfig(resolution="1920x1080", audio_languages=("en",)),
    output=OutputConfig(format=OutputFormat.MP4, overwrite=True),
    storage=StorageConfig(workspace_dir=Path("/var/tmp/rayohx"), check_space=True),
)
result = download("https://example.com/master.m3u8", "movie.mp4", config=config)
```

Wrong values raise `ConfigurationError` straight away. Secrets (header and cookie values, proxy passwords) are hidden in `repr`, logs and error messages.

## Follow progress and cancel

```python
from rayohx import Downloader, EventType

downloader = Downloader("https://example.com/master.m3u8", output="video.mp4")

def on_event(event):
    if event.type is EventType.DOWNLOAD_PROGRESS and event.progress.fraction is not None:
        print(f"{event.progress.fraction:.0%}")

downloader.events.subscribe(on_event)
result = downloader.run()          # call downloader.cancel() from another thread to stop it
```

Listeners run on the thread that produced the event, so keep them quick and hand work over to your own thread or queue. A cancelled download returns a result whose `cancelled` is true, and leaves what a later run needs to resume.

## Other helpers

- `playlist_to_dict`, `progress_to_dict` and `result_to_dict` turn results into JSON-ready dictionaries (the same documents `rayohx --json` prints, with addresses redacted).
- `configure_logging` sends the library's log records to a stream or file, with secrets removed. The library logs under the `rayohx` name and is silent until you configure it.
- `parse_playlist` and `is_hls_playlist` read playlist text without any network access.
