"""A memory hog is faulted alone, and the race carries on without it.

Every car's process caps its own address space at its size after the
imports plus `MEMORY_BUDGET_MB` (ADR 0007): an allocation past that raises
`MemoryError` in the controller that asked, and its car is DNF'd with
`MEMORY_FAULT`. The other cars are other processes, untouched.

These race on the short oval of `tests/mini_race.py`, the cars forked from
the test process. The field sits on the grid for a second of racing, long
enough for a hog to go over its budget and everyone else to still be
racing afterwards.
"""

import os
from pathlib import Path

import pytest

from cocoracer_server import car_process
from tests.mini_race import SITS, faults, racing, statuses

REPO = Path(__file__).resolve().parents[2]
BASELINES = ("pure_pursuit", "disparity_extender", "wall_follow", "starter")

pytestmark = pytest.mark.skipif(
    not os.path.exists("/proc/self/statm"), reason="the cap is sized from /proc"
)

# 32 MB more every step, every page written so it is really resident: over
# the 512 MB budget on its seventeenth step, well inside the race.
HOARDS = """
import numpy as np
from cocoracer.controller import Controller

class Car(Controller):
    def __init__(self):
        self.hoard = []

    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        self.hoard.append(np.ones(4_000_000))
        return 0.0, 0.0
"""

# One reservation bigger than the budget. `np.empty` writes no pages, so
# without the cap this would quietly succeed rather than fill the machine:
# the fault can only come from the cap.
GIANT = f"""
import numpy as np
from cocoracer.controller import Controller

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        self.giant = np.empty({(car_process.MEMORY_BUDGET_MB + 64) * 2**20},
                              dtype=np.uint8)
        return 0.0, 0.0
"""

# 640 MB held in a module global before a single step.
HOARDS_AT_IMPORT = """
import numpy as np
from cocoracer.controller import Controller

HOARD = np.ones(80_000_000)

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        return 0.0, 0.0
"""

# Just under the budget, in one go, and kept: within the rules.
NEARLY_FULL = f"""
import numpy as np
from cocoracer.controller import Controller

class Car(Controller):
    def reset(self, track_info):
        self.held = np.ones({(car_process.MEMORY_BUDGET_MB - 96) * 2**20 // 8})

    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        return 0.0, 0.0
"""


def test_a_car_that_hoards_memory_faults_alone(tmp_path: Path) -> None:
    with racing([SITS, HOARDS, SITS], tmp_path, steps=40) as (engine, _):
        assert statuses(engine) == ["racing", "dnf", "racing"]
        assert faults(engine) == [None, car_process.MEMORY_FAULT, None]


def test_one_giant_allocation_faults_only_the_car_that_made_it(
    tmp_path: Path,
) -> None:
    with racing([SITS, GIANT, SITS], tmp_path) as (engine, _):
        assert statuses(engine) == ["racing", "dnf", "racing"]
        assert faults(engine) == [None, car_process.MEMORY_FAULT, None]


def test_a_controller_that_hoards_at_import_never_takes_the_grid(
    tmp_path: Path,
) -> None:
    with racing([SITS, HOARDS_AT_IMPORT], tmp_path) as (engine, _):
        assert statuses(engine) == ["racing", "dnf"]
        assert faults(engine) == [None, car_process.MEMORY_FAULT]


def test_a_car_may_use_its_whole_budget(tmp_path: Path) -> None:
    with racing([NEARLY_FULL, SITS], tmp_path) as (engine, _):
        assert faults(engine) == [None, None]


def test_a_full_field_of_baselines_races_clean_under_the_cap(
    tmp_path: Path,
) -> None:
    sources = [
        (REPO / "cocoracer" / "controllers" / f"{name}.py").read_text()
        for name in BASELINES
    ] * 4

    with racing(sources, tmp_path, steps=40) as (engine, _):
        # A baseline may still crash out on a track it was not tuned for;
        # what it must never do is trip the cap.
        assert faults(engine) == [None] * 16
