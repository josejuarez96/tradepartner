"""#3551: wait for DNS with bounded backoff."""

from __future__ import annotations

from typing import Any

from tradepartner.netwait import wait_for_hosts


class _World:
    """A fake clock that only the fake sleep advances."""

    def __init__(self, fail: dict[str, int]) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []
        self.calls: list[tuple[str, int]] = []
        self.fail = fail

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def resolve(self, host: str, port: int) -> Any:
        self.calls.append((host, port))
        if self.fail.get(host, 0) > 0:
            self.fail[host] -= 1
            raise OSError("nodename nor servname provided")
        return [("addr",)]


def _wait(world: _World, hosts: list[str], budget: float) -> str | None:
    return wait_for_hosts(
        hosts,
        max_wait_seconds=budget,
        initial_delay_seconds=5,
        resolve=world.resolve,
        sleep=world.sleep,
        clock=world.clock,
    )


def test_waits_until_every_host_resolves() -> None:
    world = _World({"www.sec.gov": 2})
    assert _wait(world, ["www.sec.gov"], 900) is None
    assert len(world.calls) == 3
    assert world.sleeps == [5, 10]


def test_zero_budget_makes_no_resolver_call() -> None:
    world = _World({"www.sec.gov": 99})
    assert _wait(world, ["www.sec.gov"], 0) is None
    assert world.calls == [] and world.sleeps == []


def test_a_host_that_never_resolves_is_named_within_budget() -> None:
    world = _World({"down.example": 10**6})
    message = _wait(world, ["down.example", "up.example"], 60)
    assert message is not None and "down.example" in message
    assert "up.example" not in message
    assert sum(world.sleeps) <= 60
    assert world.sleeps == [5, 10, 20, 25]  # doubling, last capped by the budget


def test_every_host_is_resolved_on_port_443() -> None:
    world = _World({})
    assert _wait(world, ["a.example", "b.example", "c.example"], 60) is None
    assert world.calls == [("a.example", 443), ("b.example", 443), ("c.example", 443)]
