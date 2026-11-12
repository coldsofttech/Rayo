import threading

import pytest

from rayohx import CancellationError, CancellationToken, RateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def read(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def limiter(rate: int, clock: Clock) -> RateLimiter:
    return RateLimiter(rate, clock=clock.read, sleep=clock.sleep)


def test_rejects_a_non_positive_rate() -> None:
    with pytest.raises(ValueError, match="positive"):
        RateLimiter(0)


def test_one_second_of_data_goes_out_at_once_then_callers_wait() -> None:
    clock = Clock()
    gate = limiter(100, clock)
    assert gate.bytes_per_second == 100
    gate.acquire(100)
    assert clock.sleeps == []
    gate.acquire(100)
    assert clock.sleeps == [1.0]


def test_allowance_refills_with_time_but_never_beyond_one_second() -> None:
    clock = Clock()
    gate = limiter(100, clock)
    gate.acquire(100)
    clock.now += 60
    gate.acquire(100)
    assert clock.sleeps == []
    gate.acquire(100)
    assert clock.sleeps == [1.0]


def test_a_large_request_waits_in_proportion() -> None:
    clock = Clock()
    gate = limiter(1000, clock)
    gate.acquire(3000)
    assert clock.sleeps == [2.0]


def test_threads_share_one_limit() -> None:
    clock = Clock()
    gate = limiter(100, clock)
    threads = [threading.Thread(target=gate.acquire, args=(100,)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(clock.sleeps) == [1.0, 2.0, 3.0]


def test_waiting_stops_when_cancelled() -> None:
    clock = Clock()
    gate = limiter(100, clock)
    token = CancellationToken()
    gate.acquire(100)
    token.cancel()
    with pytest.raises(CancellationError):
        gate.acquire(500, token)
    assert clock.sleeps == []
