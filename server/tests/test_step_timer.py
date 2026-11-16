"""A controller that overruns a call's deadline faults alone (ADR 0007,
which replaced ADR 0005's timer with a deadline on each car's pipe).

These race on the short oval of `tests/mini_race.py`, the cars forked from
the test process, as the worker runs them. The limits are shortened so a
hang costs a fifth of a second rather than the event's full second. Unlike
the old `SIGALRM` timer, a deadline needs nothing from the hanging process,
so a loop that swallows every exception, or one in C that never comes back
to Python, is stopped all the same.
"""

import time
from pathlib import Path

import pytest

from cocoracer_server import car_process
from tests.mini_race import DRIVES, faults, racing, statuses

LOOPS = """
from cocoracer.controller import Controller

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        while True:
            pass
"""

# Swallows every exception, the old timer's interrupt included.
LOOPS_BEHIND_EXCEPT = """
from cocoracer.controller import Controller

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        while True:
            try:
                while True:
                    pass
            except BaseException:
                pass
"""

# A loop in C that holds the GIL and never returns to Python, where no
# signal handler could run.
LOOPS_IN_C = """
from cocoracer.controller import Controller

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        return sum(range(10**15)), 0.0
"""

SLEEPS = """
import time
from cocoracer.controller import Controller

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        time.sleep(3600)
        return 1.0, 0.0
"""

RESET_LOOPS = """
from cocoracer.controller import Controller

class Car(Controller):
    def reset(self, track_info):
        while True:
            pass

    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        return 1.0, 0.0
"""

IMPORT_LOOPS = """
from cocoracer.controller import Controller

while True:
    pass

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        return 1.0, 0.0
"""


def _state(pid: int) -> str:
    with open(f"/proc/{pid}/stat") as f:
        return f.read().rsplit(")", 1)[1].split()[0]


@pytest.fixture(autouse=True)
def short_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(car_process, "STEP_TIME_LIMIT", 0.2)
    monkeypatch.setattr(car_process, "RESET_TIME_LIMIT", 0.2)
    monkeypatch.setattr(car_process, "LOAD_TIME_LIMIT", 0.2)


@pytest.mark.parametrize("hang", [LOOPS, LOOPS_BEHIND_EXCEPT, LOOPS_IN_C, SLEEPS])
def test_a_step_that_never_returns_faults_only_its_car(
    tmp_path: Path, hang: str
) -> None:
    with racing([DRIVES, hang, DRIVES], tmp_path) as (engine, cars):
        assert statuses(engine) == ["racing", "dnf", "racing"]
        assert faults(engine) == [None, car_process.STEP_TIMEOUT_FAULT, None]
        # Killed, not left to spin: its process is already dead, a zombie
        # until the race's end reaps it.
        assert _state(cars[1].pid) == "Z"


@pytest.mark.parametrize(
    ("hang", "fault"),
    [
        (RESET_LOOPS, car_process.RESET_TIMEOUT_FAULT),
        (IMPORT_LOOPS, car_process.LOAD_TIMEOUT_FAULT),
    ],
)
def test_a_hang_before_the_start_faults_only_its_car(
    tmp_path: Path, hang: str, fault: str
) -> None:
    """In `reset()` or in the module body: the field still gets its race."""
    with racing([DRIVES, hang, DRIVES], tmp_path) as (engine, _):
        assert statuses(engine) == ["racing", "dnf", "racing"]
        assert faults(engine) == [None, fault, None]


def test_the_cars_wait_on_their_deadlines_side_by_side(tmp_path: Path) -> None:
    """Three cars hanging at import cost the field one import deadline,
    not three: each is counted from its own send."""
    started = time.monotonic()
    with racing([IMPORT_LOOPS] * 3 + [DRIVES], tmp_path, steps=1) as (engine, _):
        pass
    assert time.monotonic() - started < 3 * 0.2
    assert faults(engine)[:3] == [car_process.LOAD_TIMEOUT_FAULT] * 3


def test_the_faults_name_the_limits_in_french() -> None:
    assert car_process.STEP_TIMEOUT_FAULT == "le contrôleur n'a pas répondu en 1 s"
    assert car_process.RESET_TIMEOUT_FAULT == (
        "le contrôleur n'a pas répondu en 5 s dans reset()"
    )
    assert car_process.LOAD_TIMEOUT_FAULT == (
        "le contrôleur n'a pas été chargé en 10 s"
    )
