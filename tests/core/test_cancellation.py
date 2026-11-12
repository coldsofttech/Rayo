import threading

import pytest

from rayohx import CancellationError, CancellationToken


def test_token_starts_clear_and_cancels_once() -> None:
    token = CancellationToken()
    assert not token.is_cancelled
    token.raise_if_cancelled()
    token.cancel()
    token.cancel()
    assert token.is_cancelled
    with pytest.raises(CancellationError, match="cancelled"):
        token.raise_if_cancelled()


def test_wait_returns_at_once_when_cancelled() -> None:
    token = CancellationToken()
    assert token.wait(0.01) is False
    token.cancel()
    assert token.wait(60) is True


def test_wait_wakes_when_another_thread_cancels() -> None:
    token = CancellationToken()
    timer = threading.Timer(0.05, token.cancel)
    timer.start()
    try:
        assert token.wait(5) is True
    finally:
        timer.join()
