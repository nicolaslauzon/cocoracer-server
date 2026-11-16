"""Shared fixtures: a store, a scheduler, and the runner that fakes a race.

The three fixtures are layered so a test module bends them without
redefining them: override `teams` to seed the store, `shuffle` to change the
grid order the scheduler hands the runner.

`RecordingRunner` is a plain class, so tests that type-annotate it import it
by name -- `from tests.conftest import RecordingRunner`, with the package
prefix, because `tests/` has an `__init__.py`. So are `CAR`, the smallest
controller static analysis accepts, `enter`, which signs a team up and
pushes it into the field, and `key`, the key the store's per-team calls
take for the team that has a name now.

Every test hashes passwords at a trivial scrypt cost (`cheap_password_hashing`):
the real cost is paid on each sign-up and sign-in, and would add seconds.
"""

import os

# One BLAS thread, before anything imports numpy, as in the race worker: the
# car tests fork this process, and a fork must leave no BLAS thread behind.
for _blas in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_blas] = "1"

import threading  # noqa: E402
from collections.abc import Callable, Sequence  # noqa: E402

import pytest  # noqa: E402

from cocoracer_server import store as store_module  # noqa: E402
from cocoracer_server.runner import Entry, RaceOutcome  # noqa: E402
from cocoracer_server.scheduler import Scheduler  # noqa: E402
from cocoracer_server.store import Store, TeamKey  # noqa: E402

CAR = """
from cocoracer.controller import Controller

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        return 5.0, 0.0
"""


def enter(store: Store, name: str, source: str = CAR, password: str = "pw") -> None:
    """Sign `name` up and push `source`, so the team is in the field."""
    team = store.register(name, password)
    store.push(team.key, source)


def key(store: Store, name: str) -> TeamKey:
    """The key of the team that has `name` now."""
    team = store.account(name)
    assert team is not None
    return team.key


class RecordingRunner:
    """Records each call and returns a result the test chooses.

    By default the field finishes in reverse grid order, so every race
    produces a clear, checkable set of positions gained. Set `outcome` to
    dictate the result instead, and `gate` to keep the race running until
    the test releases it -- which is how anything is tested against a race
    in progress without one ever being run.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.outcome: RaceOutcome | None = None
        self.started = threading.Event()
        self.gate: threading.Event | None = None

    def __call__(
        self,
        entries: Sequence[Entry],
        track: str,
        laps: int | None,
        time_limit: float,
        **kwargs: object,
    ) -> RaceOutcome:
        grid = [entry.name for entry in entries]
        self.calls.append(
            {
                "grid": grid,
                "sources": {entry.name: entry.source for entry in entries},
                "track": track,
                "laps": laps,
                "time_limit": time_limit,
                "after_winner": kwargs.get("after_winner"),
                "play": kwargs.get("play"),
            }
        )
        self.started.set()
        if self.gate is not None:
            assert self.gate.wait(5.0), "the test never released the race"
        if self.outcome is not None:
            return self.outcome
        return RaceOutcome(
            classified=tuple(reversed(grid)),
            statuses={name: "finished" for name in grid},
        )


@pytest.fixture(autouse=True)
def cheap_password_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hash team passwords at scrypt's lowest cost, to keep the suite fast."""
    monkeypatch.setattr(store_module, "SCRYPT_N", 2)
    monkeypatch.setattr(store_module, "SCRYPT_R", 1)


@pytest.fixture
def teams() -> tuple[str, ...]:
    """The teams pushed into `store` before the test runs; override to seed."""
    return ()


@pytest.fixture
def store(teams: tuple[str, ...]) -> Store:
    store = Store()
    for name in teams:
        enter(store, name)
    return store


@pytest.fixture
def runner() -> RecordingRunner:
    return RecordingRunner()


@pytest.fixture
def shuffle() -> Callable[[list[str]], None]:
    """Leave the grid in registration order; override to shuffle it."""
    return lambda names: None


@pytest.fixture
def scheduler(
    store: Store, runner: RecordingRunner, shuffle: Callable[[list[str]], None]
) -> Scheduler:
    return Scheduler(store, runner=runner, shuffle=shuffle)
