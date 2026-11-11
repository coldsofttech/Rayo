import logging
import threading

import pytest

from rayohx import (
    DownloadEvent,
    DownloadState,
    DownloadStateMachine,
    EventBus,
    EventType,
    InvalidStateTransitionError,
)
from rayohx.events import TRANSITIONS


def _event(kind: EventType = EventType.DOWNLOAD_STARTED) -> DownloadEvent:
    return DownloadEvent(type=kind, download_id="d1", state=DownloadState.QUEUED)


def test_every_state_has_transitions_and_terminals_end() -> None:
    assert set(TRANSITIONS) == set(DownloadState)
    for state in DownloadState:
        assert (not TRANSITIONS[state]) == state.is_terminal


def test_happy_path() -> None:
    machine = DownloadStateMachine()
    for state in (
        DownloadState.INSPECTING,
        DownloadState.PREPARING,
        DownloadState.DOWNLOADING,
        DownloadState.PAUSED,
        DownloadState.DOWNLOADING,
        DownloadState.FINALIZING,
        DownloadState.COMPLETED,
    ):
        assert machine.can_move_to(state)
        machine.move_to(state)
    assert machine.state is DownloadState.COMPLETED


def test_move_returns_previous_state_and_rejects_bad_moves() -> None:
    machine = DownloadStateMachine()
    assert machine.move_to(DownloadState.INSPECTING) is DownloadState.QUEUED
    with pytest.raises(InvalidStateTransitionError, match="inspecting to completed"):
        machine.move_to(DownloadState.COMPLETED)
    assert machine.state is DownloadState.INSPECTING


@pytest.mark.parametrize("end", [DownloadState.FAILED, DownloadState.CANCELLED])
def test_terminal_state_is_final(end: DownloadState) -> None:
    machine = DownloadStateMachine()
    machine.move_to(end)
    assert not machine.can_move_to(DownloadState.QUEUED)
    with pytest.raises(InvalidStateTransitionError):
        machine.move_to(DownloadState.INSPECTING)


def test_concurrent_moves_allow_exactly_one_winner() -> None:
    machine = DownloadStateMachine(DownloadState.DOWNLOADING)
    wins: list[DownloadState] = []

    def attempt(target: DownloadState) -> None:
        try:
            machine.move_to(target)
            wins.append(target)
        except InvalidStateTransitionError:
            pass

    threads = [
        threading.Thread(target=attempt, args=(target,))
        for target in (DownloadState.FAILED, DownloadState.CANCELLED, DownloadState.FINALIZING)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(wins) >= 1
    assert machine.state in wins


def test_event_data_is_frozen() -> None:
    event = DownloadEvent(
        type=EventType.SEGMENT_COMPLETED,
        download_id="d",
        state=DownloadState.DOWNLOADING,
        segment_index=3,
        data={"bytes": 10},
    )
    assert event.data["bytes"] == 10
    with pytest.raises(TypeError):
        event.data["bytes"] = 1  # type: ignore[index]
    assert event.timestamp > 0


def test_bus_delivers_in_order_and_unsubscribes() -> None:
    bus = EventBus()
    seen: list[str] = []
    stop_first = bus.subscribe(lambda event: seen.append(f"a:{event.type.value}"))
    bus.subscribe(lambda event: seen.append(f"b:{event.type.value}"))
    bus.emit(_event())
    stop_first()
    stop_first()
    bus.emit(_event(EventType.DOWNLOAD_COMPLETED))
    assert seen == ["a:download_started", "b:download_started", "b:download_completed"]


def test_failing_listener_does_not_stop_others(caplog: pytest.LogCaptureFixture) -> None:
    bus = EventBus()
    seen: list[EventType] = []

    def broken(_event: DownloadEvent) -> None:
        raise RuntimeError("boom")

    bus.subscribe(broken)
    bus.subscribe(lambda event: seen.append(event.type))
    with caplog.at_level(logging.ERROR, logger="rayohx"):
        bus.emit(_event())
    assert seen == [EventType.DOWNLOAD_STARTED]
    assert "listener failed" in caplog.text
