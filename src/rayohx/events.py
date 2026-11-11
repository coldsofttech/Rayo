"""The download state machine and the event bus.

The engine never prints. It reports what happens through typed events, and every front end (command
line, API, desktop window) subscribes to the same stream.
"""

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from rayohx.enums import DownloadState, EventType
from rayohx.errors import InvalidStateTransitionError
from rayohx.models import DownloadProgress

logger = logging.getLogger(__name__)

_S = DownloadState

#: States each state may move to. Terminal states move nowhere.
TRANSITIONS: Mapping[DownloadState, frozenset[DownloadState]] = MappingProxyType(
    {
        _S.QUEUED: frozenset({_S.INSPECTING, _S.CANCELLED, _S.FAILED}),
        _S.INSPECTING: frozenset({_S.PREPARING, _S.CANCELLED, _S.FAILED}),
        _S.PREPARING: frozenset({_S.DOWNLOADING, _S.CANCELLED, _S.FAILED}),
        _S.DOWNLOADING: frozenset({_S.FINALIZING, _S.PAUSED, _S.CANCELLED, _S.FAILED}),
        _S.PAUSED: frozenset({_S.DOWNLOADING, _S.CANCELLED, _S.FAILED}),
        _S.FINALIZING: frozenset({_S.COMPLETED, _S.CANCELLED, _S.FAILED}),
        _S.COMPLETED: frozenset(),
        _S.FAILED: frozenset(),
        _S.CANCELLED: frozenset(),
    }
)


class DownloadStateMachine:
    """Holds the state of one download and allows only valid moves. Safe across threads."""

    def __init__(self, state: DownloadState = DownloadState.QUEUED) -> None:
        self._state = state
        self._lock = threading.Lock()

    @property
    def state(self) -> DownloadState:
        """The current state."""
        with self._lock:
            return self._state

    def can_move_to(self, new_state: DownloadState) -> bool:
        """Whether ``new_state`` is reachable from the current state."""
        return new_state in TRANSITIONS[self.state]

    def move_to(self, new_state: DownloadState) -> DownloadState:
        """Move to ``new_state`` and return the state it left.

        Raises:
            InvalidStateTransitionError: If the move is not allowed.
        """
        with self._lock:
            old = self._state
            if new_state not in TRANSITIONS[old]:
                raise InvalidStateTransitionError(
                    f"A download cannot go from {old.value} to {new_state.value}."
                )
            self._state = new_state
            return old


@dataclass(frozen=True, slots=True, kw_only=True)
class DownloadEvent:
    """Something that happened during a download."""

    type: EventType
    download_id: str
    state: DownloadState
    timestamp: float = field(default_factory=time.time)
    progress: DownloadProgress | None = None
    segment_index: int | None = None
    message: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


EventListener = Callable[[DownloadEvent], None]


class EventBus:
    """Delivers events to listeners. A listener that raises never stops the download."""

    def __init__(self) -> None:
        self._listeners: list[EventListener] = []
        self._lock = threading.Lock()

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        """Add ``listener`` and return a function that removes it again."""
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def emit(self, event: DownloadEvent) -> None:
        """Deliver ``event`` to every listener, in the order they subscribed."""
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                logger.exception("An event listener failed on %s", event.type.value)
