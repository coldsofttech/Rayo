"""The ways a downloaded stream becomes an output file.

* **Plain**: the segments are joined as they are (MPEG-TS into ``.ts``, fragmented MP4 into
  ``.mp4``) or saved one file each.
* **Native mux**: the built-in remuxer re-wraps the stream into a fragmented MP4, joining in any
  separate audio tracks, which are first saved beside the output and read as needed.
* **FFmpeg**: for codecs the built-in remuxer cannot copy, an FFmpeg already installed copies the
  downloaded pieces into an MP4. It is never required, bundled or asked to transcode.

Subtitles are written next to the output as ``.vtt`` files.
"""

import dataclasses
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rayohx.cancellation import CancellationToken
from rayohx.config import DownloadConfig
from rayohx.containers import validate_fmp4_file
from rayohx.enums import ContainerFormat
from rayohx.errors import OutputExistsError, OutputFormatError
from rayohx.events import EventBus
from rayohx.http_client import HttpClient
from rayohx.media_download import DownloadSummary, MediaDownload
from rayohx.models import AudioTrack, MediaPlaylist, SubtitleTrack, Variant
from rayohx.mux import ffmpeg
from rayohx.mux.sink import MuxSink, TrackFile
from rayohx.mux.webvtt import merge_segments
from rayohx.output import OutputPlan, sanitize_filename
from rayohx.sinks import DirectorySink, FileSink, OutputSink, StreamSink

logger = logging.getLogger(__name__)

_EXTENSION = {ContainerFormat.MPEG_TS: ".ts", ContainerFormat.FMP4: ".mp4"}


@dataclass(frozen=True, slots=True)
class Rendition:
    """A separate audio track: its description, its playlist and what its segments are."""

    track: AudioTrack
    media: MediaPlaylist
    container: ContainerFormat
    #: The codecs of the track's audio streams (MPEG-TS only; filled in when FFmpeg is used).
    codecs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Delivery:
    """Everything a delivery needs from the running download."""

    client: HttpClient
    config: DownloadConfig
    bus: EventBus | None
    download_id: str
    cancel: CancellationToken
    on_finalizing: Callable[[], None]

    def _media_download(
        self,
        media: MediaPlaylist,
        *,
        sink: OutputSink,
        variant: Variant | None = None,
        container: ContainerFormat | None = None,
        output_container: ContainerFormat | None = None,
        on_finalizing: Callable[[], None] | None = None,
        config: DownloadConfig | None = None,
    ) -> MediaDownload:
        return MediaDownload(
            media,
            client=self.client,
            config=config or self.config,
            sink=sink,
            variant=variant,
            events=self.bus,
            download_id=self.download_id,
            cancel=self.cancel,
            container=container,
            output_container=output_container,
            on_finalizing=on_finalizing,
        )

    # --- plain -----------------------------------------------------------------------------

    def plain(
        self,
        media: MediaPlaylist,
        container: ContainerFormat,
        plan: OutputPlan,
        variant: Variant | None,
    ) -> DownloadSummary:
        """Join the segments as they are, or save them as separate files."""
        overwrite = self.config.output.overwrite
        sink = (
            DirectorySink(plan.path, container, overwrite=overwrite)
            if plan.is_directory
            else FileSink(plan.path, overwrite=overwrite)
        )
        return self._media_download(
            media,
            sink=sink,
            variant=variant,
            container=container,
            on_finalizing=self.on_finalizing,
        ).run()

    # --- native mux ------------------------------------------------------------------------

    def native_mux(
        self,
        media: MediaPlaylist,
        container: ContainerFormat,
        plan: OutputPlan,
        variant: Variant | None,
        audios: list[Rendition],
    ) -> tuple[DownloadSummary, float | None]:
        """Re-wrap the stream, and any separate audio, into a fragmented MP4.

        Returns the summary and the seconds taken off the timeline (for subtitles).
        """
        saved: list[Path] = []
        try:
            extras = []
            for number, audio in enumerate(audios):
                path = self._temporary(plan.path, f"audio{number}", audio.container)
                saved.append(path)
                self._save(audio, path)
                extras.append(
                    TrackFile(path, audio.container, audio.track.language, audio.track.name)
                )
            sink = MuxSink(
                plan.path, container, extras=extras, overwrite=self.config.output.overwrite
            )
            summary = self._media_download(
                media,
                sink=sink,
                variant=variant,
                container=container,
                output_container=ContainerFormat.FMP4,
                on_finalizing=self.on_finalizing,
            ).run()
            return summary, sink.origin
        finally:
            _remove(saved)

    # --- FFmpeg ----------------------------------------------------------------------------

    def with_ffmpeg(
        self,
        media: MediaPlaylist,
        container: ContainerFormat,
        plan: OutputPlan,
        variant: Variant | None,
        audios: list[Rendition],
        executable: Path,
        video_audio: tuple[str, ...] = (),
    ) -> DownloadSummary:
        """Download the pieces to files, then have FFmpeg copy them into one MP4."""
        saved: list[Path] = []
        part = plan.path.with_name(plan.path.name + ".part")
        try:
            video_path = self._temporary(plan.path, "video", container)
            saved.append(video_path)
            summary = self._media_download(
                media,
                sink=FileSink(video_path, overwrite=True),
                variant=variant,
                container=container,
                config=dataclasses.replace(self.config, resume=False),
            ).run()
            for number, audio in enumerate(audios):
                path = self._temporary(plan.path, f"audio{number}", audio.container)
                saved.append(path)
                self._save(audio, path)
            self.on_finalizing()
            self._check_free(plan.path)
            inputs = [ffmpeg.FfmpegInput(video_path, video_audio)]
            inputs += [
                ffmpeg.FfmpegInput(path, audio.codecs, audio.track.language)
                for path, audio in zip(saved[1:], audios, strict=True)
            ]
            ffmpeg.remux(executable, inputs, part, cancel=self.cancel)
            validate_fmp4_file(part)
            self._place(part, plan.path)
            return dataclasses.replace(summary, output=plan.path)
        finally:
            part.unlink(missing_ok=True)
            _remove(saved)

    def _check_free(self, target: Path) -> None:
        if target.exists() and not self.config.output.overwrite:
            raise OutputExistsError(f"The output file already exists: {target}")

    def _place(self, part: Path, target: Path) -> None:
        self._check_free(target)
        os.replace(part, target)

    # --- pieces ----------------------------------------------------------------------------

    def _save(self, audio: Rendition, path: Path) -> None:
        """Download a separate track into a file, in full, with nothing kept for resuming."""
        self._media_download(
            audio.media,
            sink=FileSink(path, overwrite=True),
            container=audio.container,
            config=dataclasses.replace(self.config, resume=False),
        ).run()

    @staticmethod
    def _temporary(output: Path, label: str, container: ContainerFormat) -> Path:
        suffix = _EXTENSION.get(container, ".bin")
        return output.with_name(f".{output.name}.{label}{suffix}")

    # --- subtitles -------------------------------------------------------------------------

    def subtitles(
        self,
        tracks: list[tuple[SubtitleTrack, MediaPlaylist, Path]],
        shift: float,
    ) -> list[Path]:
        """Download each subtitle track and write it as a ``.vtt`` file."""
        written = []
        for track, media, path in tracks:
            texts: list[bytes] = []
            self._media_download(
                media,
                sink=StreamSink(texts.append),
                config=dataclasses.replace(self.config, resume=False),
            ).run()
            try:
                merged = merge_segments(
                    [text.decode("utf-8", errors="replace") for text in texts], shift
                )
            except OutputFormatError as exc:
                raise OutputFormatError(
                    f"The subtitles {track.name!r} are not in a supported form.",
                    hint=exc.message,
                ) from exc
            part = path.with_name(path.name + ".part")
            part.write_text(merged, encoding="utf-8")
            self._place(part, path)
            written.append(path)
        return written


def subtitle_path(output: Path, track: SubtitleTrack, used: set[Path]) -> Path:
    """Where a subtitle track is saved: ``<output name>.<language>.vtt``, made unique."""
    label = sanitize_filename(track.language or track.name, "subtitles")
    path = output.with_suffix(f".{label}.vtt")
    count = 2
    while path in used:
        path = output.with_suffix(f".{label}{count}.vtt")
        count += 1
    used.add(path)
    return path


def check_free(paths: list[Path], overwrite: bool) -> None:
    """Fail early if a file we are about to write already exists."""
    if overwrite:
        return
    for path in paths:
        if path.exists():
            raise OutputExistsError(
                f"The output file already exists: {path}",
                hint="Choose another name or allow overwriting.",
            )


def _remove(paths: list[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)
        path.with_name(path.name + ".part").unlink(missing_ok=True)
