"""Resume state: what is already done, saved safely so an interrupted download can continue.

The file holds no secrets: only a fingerprint of the playlist, counters and a redacted address.
It is written atomically (a temporary file renamed over the old one), so a crash never leaves a
half-written state behind.
"""

import hashlib
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from rayohx.models import Segment

logger = logging.getLogger(__name__)

STATE_VERSION = 1


def playlist_signature(segments: Sequence[Segment]) -> str:
    """A fingerprint of what will be downloaded, stable across signed-link changes.

    Query strings are left out, so a refreshed token does not stop a resume. A playlist whose
    segments, order, byte ranges or key addresses differ gets a different fingerprint, so state
    from another stream is never reused.
    """
    digest = hashlib.sha256()
    for segment in segments:
        parts = [
            urlsplit(segment.uri)._replace(query="", fragment="").geturl(),
            f"{segment.duration:.3f}",
            str(segment.byte_range),
            urlsplit(segment.init_map.uri).path if segment.init_map else "",
            str(segment.init_map.byte_range) if segment.init_map else "",
            segment.encryption.method.value if segment.encryption else "",
            urlsplit(segment.encryption.uri or "").path if segment.encryption else "",
        ]
        digest.update("\x1f".join(parts).encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class ResumeState:
    """Progress of one download."""

    signature: str
    total_segments: int
    strategy: str
    #: Segments before this index are written to the output.
    next_index: int = 0
    #: Bytes of output those segments take.
    written_bytes: int = 0
    source: str = ""
    updated: str = ""
    version: int = STATE_VERSION


class StateFile:
    """Reads and writes a ``ResumeState`` at a fixed path."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self, *, signature: str, total_segments: int, strategy: str) -> ResumeState | None:
        """Return the saved state if it belongs to this download, else ``None``.

        Unreadable, outdated or foreign state is ignored (and logged), never trusted.
        """
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            logger.warning("Ignoring unreadable resume state in %s.", self.path)
            return None
        try:
            state = ResumeState(**raw)
        except TypeError:
            logger.warning("Ignoring resume state with unexpected contents in %s.", self.path)
            return None
        valid = (
            state.version == STATE_VERSION
            and state.signature == signature
            and state.total_segments == total_segments
            and state.strategy == strategy
            and 0 <= state.next_index <= total_segments
            and state.written_bytes >= 0
        )
        if not valid:
            logger.info("Ignoring resume state that does not match this download.")
            return None
        return state

    def save(self, state: ResumeState) -> None:
        """Write ``state`` atomically."""
        data = asdict(state)
        data["updated"] = datetime.now(UTC).isoformat(timespec="seconds")
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
