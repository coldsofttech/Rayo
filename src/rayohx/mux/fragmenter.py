"""Cutting a track's samples into fragments, and keeping its timeline continuous."""

from collections.abc import Callable

from rayohx.mux.fmp4 import build_fragment
from rayohx.mux.model import Sample, TrackConfig

#: A video fragment is cut at a keyframe once it holds at least this many seconds.
MIN_VIDEO_FRAGMENT = 1.0
#: An audio fragment is cut once it holds this many seconds.
AUDIO_FRAGMENT = 2.0
#: No fragment grows past this many bytes, keyframe or not.
MAX_FRAGMENT_BYTES = 16 * 1024 * 1024
#: A jump in timestamps larger than this many seconds is treated as a discontinuity.
MAX_JUMP_SECONDS = 5.0
DEFAULT_VIDEO_DURATION = 3000  # 30 fps at 90 kHz, used only when nothing better is known


class TrackFragmenter:
    """Collects samples and hands finished fragments to ``emit(fragment, start_ticks)``.

    Video samples arrive without a duration; it is filled in from the next sample's decode time.
    When timestamps jump (an ad break, or a stream restarted with new timestamps) the timeline is
    shifted so the track stays continuous.
    """

    def __init__(
        self,
        track_id: int,
        config: TrackConfig,
        emit: Callable[[bytes, int], None],
    ) -> None:
        self.track_id = track_id
        self.config = config
        self._emit = emit
        self._video = config.kind == "video"
        self._held: Sample | None = None
        self._offset = 0
        self._last_duration = 0
        self._samples: list[Sample] = []
        self._bytes = 0
        self._duration = 0
        self.sample_count = 0

    def add(self, sample: Sample) -> None:
        """Add the next sample, in decode order."""
        if not self._video:
            self._push(sample)
            return
        held = self._held
        self._held = sample
        if held is None:
            return
        delta = sample.dts - held.dts
        limit = int(MAX_JUMP_SECONDS * self.config.timescale)
        if 0 < delta <= limit:
            held.duration = self._last_duration = delta
            self._push(held)
        else:
            held.duration = self._last_duration or DEFAULT_VIDEO_DURATION
            self._push(held)
            self._offset += delta - held.duration

    def finish(self) -> None:
        """Flush the last samples."""
        if self._held is not None:
            held, self._held = self._held, None
            held.duration = self._last_duration or DEFAULT_VIDEO_DURATION
            self._push(held)
        self._cut()

    def _push(self, sample: Sample) -> None:
        scale = self.config.timescale
        long_enough = self._duration >= MIN_VIDEO_FRAGMENT * scale
        if self._video and sample.sync and self._samples and long_enough:
            self._cut()
        if not self._video and self._duration >= AUDIO_FRAGMENT * scale:
            self._cut()
        if self._bytes >= MAX_FRAGMENT_BYTES:
            self._cut()
        sample.dts -= self._offset
        self._samples.append(sample)
        self._bytes += len(sample.data)
        self._duration += sample.duration
        self.sample_count += 1

    def _cut(self) -> None:
        if not self._samples:
            return
        samples, self._samples = self._samples, []
        self._bytes = 0
        self._duration = 0
        start = samples[0].dts
        self._emit(build_fragment(self.track_id, samples, max(0, start)), max(0, start))
