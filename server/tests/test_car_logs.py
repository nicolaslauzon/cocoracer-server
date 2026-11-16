"""What a controller prints goes to its own car's log, stamped with the race
time, and nowhere else.

Each car prints in its own process (ADR 0007), which gathers what it prints
under `Log`'s rules and hands it back with each reply; the worker stamps
each reply's lines with the race time of the call. These race on the short
oval of `tests/mini_race.py`; that the logs cross the worker's own process
boundary is `test_runner`'s.
"""

from pathlib import Path
from typing import Any

import pytest
from cocoracer.field import ControllerFault

from cocoracer_server import car_process
from tests.mini_race import (
    COUNTDOWN_TICKS,
    TICK_DT,
    faults,
    racing,
    started,
    step_car,
)

# Prints at import, in reset, and on every step, to both streams.
CHATTY = """
import sys
from cocoracer.controller import Controller

print("loaded")

class Car(Controller):
    def reset(self, track_info):
        print("reset", end="")

    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        print("step")
        print("careful", file=sys.stderr)
        return 1.0, 0.0
"""

QUIET = """
from cocoracer.controller import Controller

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        return 1.0, 0.0
"""


def _logs(sources: list[str], directory: Path, steps: int = 3) -> list[dict[str, Any]]:
    with racing(sources, directory, steps=steps) as (_, cars):
        return [car.log.to_json() for car in cars]


def test_each_line_is_stamped_with_the_race_time_of_its_call(tmp_path: Path) -> None:
    [log] = _logs([CHATTY], tmp_path)
    lines = log["lines"]
    # The import and reset come before the race's clock starts.
    assert lines[:2] == [[0.0, "loaded"], [0.0, "reset"]]
    steps = lines[2:]
    assert [text for _, text in steps] == ["step", "careful"] * 3
    first_step = round((COUNTDOWN_TICKS + 1) * TICK_DT, 3)
    assert steps[0][0] == first_step
    times = [t for t, _ in steps]
    assert times == sorted(times) and times[-1] > first_step
    assert log["truncated"] is False


def test_a_car_gets_only_what_it_printed(tmp_path: Path) -> None:
    other = CHATTY.replace('"loaded"', '"other loaded"')
    chatty, quiet, other_log = _logs([CHATTY, QUIET, other], tmp_path)
    assert quiet["lines"] == []
    assert chatty["lines"][0][1] == "loaded"
    assert other_log["lines"][0][1] == "other loaded"
    assert len(chatty["lines"]) == len(other_log["lines"])


def test_a_raise_leaves_its_traceback_in_the_log(tmp_path: Path) -> None:
    source = QUIET.replace("return 1.0, 0.0", "return [][0]")
    with racing([source], tmp_path, steps=1) as (engine, [car]):
        assert faults(engine) == ["IndexError: list index out of range"]
    text = [line for _, line in car.log.lines]
    assert text[0] == "Traceback (most recent call last):"
    assert text[1].endswith('car0.py", line 6, in step')
    assert text[-1] == "IndexError: list index out of range"
    # Only the team's own frames: none of the worker's.
    assert not any("car_process.py" in line for line in text)


def test_a_log_over_its_budget_keeps_its_start_and_says_so(
    tmp_path: Path,
) -> None:
    source = QUIET.replace(
        "        return 1.0, 0.0",
        "        for _ in range(10):\n"
        "            print('x' * 999)\n"
        "        return 1.0, 0.0",
    )
    with racing([source], tmp_path, steps=60) as (engine, [car]):
        assert faults(engine) == [None]
    log = car.log.to_json()
    assert log["truncated"] is True
    assert len(log["lines"]) == car_process.LOG_BUDGET_CHARS // 1000


@pytest.mark.parametrize(
    "forged",
    [
        # A line longer than any the car's own log would let through.
        "['x' * 5000]",
        # More text than the whole budget, in one reply.
        f"['x' * 1000] * {car_process.LOG_BUDGET_CHARS // 1000 + 2}",
    ],
)
def test_a_forged_log_faults_its_car(tmp_path: Path, forged: str) -> None:
    source = f"""
import json, os, struct, time
from cocoracer.controller import Controller

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        reply = json.dumps({{"c": [1.0, 0.0], "o": {forged}, "t": False}}).encode()
        os.write(4, struct.pack(">I", len(reply)) + reply)
        time.sleep(5)
"""
    with started([source], tmp_path) as [car]:
        with pytest.raises(ControllerFault):
            step_car(car, None)  # type: ignore[arg-type]
    assert car.fault == car_process.GARBLED_FAULT


def test_a_long_line_is_cut() -> None:
    log = car_process.Log()
    log.write("x" * (car_process.LOG_LINE_CHARS * 3) + "\nnext\n")
    [(_, long), (_, short)] = log.lines
    assert long == "x" * car_process.LOG_LINE_CHARS + "…"
    assert short == "next"


def test_a_log_over_its_budget_keeps_its_start_and_says_so_in_one_process() -> None:
    log = car_process.Log(budget=12)
    for i in range(10):
        log.write(f"line {i}\n")
        log.end()
    assert log.to_json() == {"lines": [[0.0, "line 0"]], "truncated": True}
