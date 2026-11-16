"""The cadence, end to end, over the path the event actually runs.

`test_scheduler.py` drives the cadence with a fake runner and
`test_runner.py` drives the subprocess directly, so between them nothing
covered `Scheduler` -> `run_race_in_subprocess` -> `race_worker`'s live
loop in one piece -- the exact path that runs on the afternoon, and
the path two shipped bugs lived on. This is the one test that does.

It costs about four seconds, which makes it the slowest test in either
suite. Most of that is the real three-second countdown, paced against the
wall clock because that is the thing being tested. The race itself is a
few ticks: both cars are timed out rather than raced to a finish, since
what is under test is the wiring, not the classification. The policy
numbers the scheduler picks (no lap target, a 63-second limit with the
countdown) stay asserted in `test_scheduler.py`, where a fake makes them
free.
"""

import json
import socket

import pytest

from cocoracer_server.runner import Entry, RaceOutcome, run_race_in_subprocess
from cocoracer_server.scheduler import Scheduler
from cocoracer_server.store import Store

DRIVES = """
from cocoracer.controller import Controller

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        return 6.0, 0.0
"""

# Long enough that the cars are released and moving, short enough that the
# race is over almost as soon as it starts.
_SIM_SECONDS = 0.5


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))  # where the worker binds the live view
        return int(s.getsockname()[1])


def _short_race(
    entries: list[Entry], track: str, laps: int, time_limit: float, **kwargs: object
) -> RaceOutcome:
    """The scheduler's own runner, with the race shrunk to a few ticks.

    The final-frame hold and the pre-roll on the grid are dropped too: four
    seconds of them are the right thing on the day and dead weight in a
    test; `test_runner.py` covers the pre-roll.
    """
    return run_race_in_subprocess(
        entries,
        track,
        laps=1,
        time_limit=_SIM_SECONDS,
        hold=0.0,
        preroll=0.0,
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.fixture
def store() -> Store:
    store = Store()
    for name in ("alpha", "bravo"):
        team = store.register(name, "pw")
        store.push(team.key, DRIVES)
    return store


def test_a_live_qualification_runs_through_the_real_subprocess(store: Store) -> None:
    scheduler = Scheduler(store, runner=_short_race, live=True, port=_free_port())

    report = scheduler.run_qualification()

    assert report is not None
    assert not report.void, report.void_reason
    assert set(report.record.grid) == {"alpha", "bravo"}
    assert set(report.record.classified) == {"alpha", "bravo"}
    # Every entrant came back with a status the worker read off the race,
    # which is what proves the result crossed the process boundary intact.
    assert set(report.statuses) == {"alpha", "bravo"}
    assert report.faults == {}
    # The live loop was recorded too, through to the race's last tick.
    kept = store.replay(report.index)
    assert isinstance(kept, bytes)
    replay = json.loads(kept)
    assert replay["names"] == list(report.record.grid)
    assert replay["frames"][-1]["t"] >= _SIM_SECONDS
    # And the cadence's own bookkeeping ran on a real outcome.
    assert len(store.races()) == 1
    assert scheduler.running_race is None
