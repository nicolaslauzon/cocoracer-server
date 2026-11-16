"""One car's controller in its own process: whatever it does there, its
car is DNF'd with a French reason and the others go on (ADR 0007).

These drive the worker's own car wrapper (`car_field.Car`) by direct
calls, its processes forked from the test process, and race on the short
oval of `tests/mini_race.py` when a test needs the engine; no full race.
"""

import ctypes
import os
import signal
import time
from pathlib import Path

import numpy as np
import pytest
from cocoracer.config import DEFAULT_PARAMS, load_config
from cocoracer.controller import Controller, load_controller
from cocoracer.engine import RaceEngine
from cocoracer.field import ControllerFault

from cocoracer_server import car_process, sandbox
from cocoracer_server.car_field import Car
from tests.mini_race import (
    COUNTDOWN_TICKS,
    DRIVES,
    SITS,
    faults,
    mini_track,
    on_the_grid,
    racing,
    reset_car,
    started,
    statuses,
    step_car,
)

pytestmark = pytest.mark.skipif(
    not os.path.exists("/proc/self/statm"), reason="cars need Linux"
)

STOPPED = car_process.STOPPED_FAULT
REFUSED = "PermissionError: [Errno 1] Operation not permitted"
SCAN = np.full(72, 5.0)


def _step(car: Car) -> object:
    return step_car(car, SCAN)


def _reset(car: Car) -> None:
    reset_car(car)


def _in(where: str, code: str) -> str:
    """A controller that runs `code` at import, in `reset` or in `step`."""
    lines = {"import": "", "reset": "pass", "step": "return 1.0, 0.0"}
    lines[where] = code
    return f"""
import os, signal, sys
from cocoracer.controller import Controller

{lines["import"]}

class Car(Controller):
    def reset(self, track_info):
        {lines["reset"]}

    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        {lines["step"]}
"""


def _elsewhere(tmp_path: Path) -> Path:
    """A second directory for a second group of cars, whose files would
    otherwise take the first group's names."""
    other = tmp_path / "other"
    other.mkdir()
    return other


def _fault_of(car: Car, where: str) -> str | None:
    """Make `car` reach `where`; the reason it was faulted, or None."""
    try:
        if where in ("reset", "step"):
            _reset(car)
        if where == "step":
            _step(car)
    except ControllerFault as exc:
        assert car.fault == str(exc)
    return car.fault


# --- a car that ends its own process -----------------------------------------


@pytest.mark.parametrize("where", ["import", "reset", "step"])
@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("sys.exit(1)", f"{STOPPED} (code de sortie 1)"),
        ("os._exit(1)", f"{STOPPED} (code de sortie 1)"),
        ("os.kill(os.getpid(), 9)", f"{STOPPED} (tué par le signal SIGKILL)"),
    ],
)
def test_a_car_that_ends_its_own_process_is_dnf_with_a_reason(
    tmp_path: Path, where: str, code: str, reason: str
) -> None:
    with started([_in(where, code), DRIVES], tmp_path) as (car, other):
        assert _fault_of(car, where) == reason
        _reset(other)
        assert _step(other) == (1.0, 0.0)


def test_a_car_that_ignores_sigalrm_and_hangs_is_dnf_at_its_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There is no timer left to disarm: the deadline is on the pipe."""
    monkeypatch.setattr(car_process, "STEP_TIME_LIMIT", 0.2)
    source = _in(
        "step",
        "signal.signal(signal.SIGALRM, signal.SIG_IGN)\n        while True: pass",
    )
    with started([source], tmp_path) as [car]:
        assert _fault_of(car, "step") == car_process.STEP_TIMEOUT_FAULT


# --- the sandbox -------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        "os.kill(os.getppid(), 0)",
        "os.kill(0, 0)",
        "import socket; socket.socket()",
        "import subprocess; subprocess.run(['true'])",
        "os.fork()",
        "os.posix_spawn('/bin/true', ['true'], {})",
    ],
)
def test_a_car_cannot_signal_others_open_a_socket_or_start_a_process(
    tmp_path: Path, code: str
) -> None:
    with started([_in("step", code)], tmp_path) as [car]:
        fault = _fault_of(car, "step")
    assert fault is not None and fault.startswith(REFUSED)
    # And it can read why in its log.
    assert car.log.lines[-1][1] == fault


def test_a_car_cannot_kill_a_sibling(tmp_path: Path) -> None:
    with started([DRIVES], tmp_path) as [victim]:
        killer = _in("step", f"os.kill({victim.pid}, signal.SIGKILL)")
        with started([killer], _elsewhere(tmp_path)) as [car]:
            assert _fault_of(car, "step") == REFUSED
        _reset(victim)
        assert _step(victim) == (1.0, 0.0)


IOPRIO_SET, IOPRIO_GET = 251, 252  # x86_64
IOPRIO_WHO_PROCESS, IOPRIO_WHO_PGRP, IOPRIO_WHO_USER = 1, 2, 3
IOPRIO_IDLE = 3 << 13
# A class the kernel rejects (EINVAL) after the filter lets the call by.
IOPRIO_INVALID = 7 << 13


def _ioprio_call(*args: object) -> str:
    """Car code calling `ioprio_set`, raising as Python would."""
    return f"""import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.syscall({IOPRIO_SET}, {", ".join(map(str, args))}) < 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))"""


def _ioprio(pid: int = 0) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    return int(libc.syscall(IOPRIO_GET, IOPRIO_WHO_PROCESS, pid))


# These reach the test run's whole process group and every process of the
# user running it should the filter let them by, so each is made harmless
# there: niceness 0, which an unprivileged call can only fail to lower to,
# and an ioprio class the kernel rejects. The filter refuses each before
# the kernel reads the value.
@pytest.mark.parametrize(
    "code",
    [
        "os.setpriority(os.PRIO_PGRP, 0, 0)",
        "os.setpriority(os.PRIO_USER, 0, 0)",
        "os.setpriority(os.PRIO_PGRP, os.getpgid(0), 0)",
        "os.setpriority(os.PRIO_USER, os.getuid(), 0)",
        _ioprio_call(IOPRIO_WHO_PGRP, 0, IOPRIO_INVALID),
        _ioprio_call(IOPRIO_WHO_USER, 0, IOPRIO_INVALID),
        "os.kill(0, 0)",
        "os.kill(-1, 0)",
        "os.killpg(0, 0)",
        "os.killpg(os.getpgid(0), 0)",
    ],
    ids=lambda code: code.split("syscall(")[-1].split(")")[0],
)
def test_a_car_cannot_reach_its_process_group_or_its_user(
    tmp_path: Path, code: str
) -> None:
    """Where a call takes 0 for "myself" by pid, another `which` makes it
    "my group" or "my user": the worker, the siblings and the server."""
    with started([_in("step", code)], tmp_path) as [car]:
        assert _fault_of(car, "step") == REFUSED


@pytest.mark.parametrize(
    "code",
    [
        "os.setpriority(os.PRIO_PROCESS, {pid}, 19)",
        _ioprio_call(IOPRIO_WHO_PROCESS, "{pid}", IOPRIO_IDLE),
    ],
    ids=["setpriority", "ioprio_set"],
)
def test_a_car_cannot_lower_a_siblings_priority(tmp_path: Path, code: str) -> None:
    with started([DRIVES], tmp_path) as [victim]:
        nice, ioprio = os.getpriority(os.PRIO_PROCESS, victim.pid), _ioprio(victim.pid)
        hostile = _in("step", code.format(pid=victim.pid))
        with started([hostile], _elsewhere(tmp_path)) as [car]:
            assert _fault_of(car, "step") == REFUSED
        assert os.getpriority(os.PRIO_PROCESS, victim.pid) == nice
        assert _ioprio(victim.pid) == ioprio


def test_a_car_may_lower_its_own_priority(tmp_path: Path) -> None:
    """Harmless, and what pid 0 means to a car calling by pid."""
    nice = max(10, os.getpriority(os.PRIO_PROCESS, 0))
    source = _in(
        "step",
        f"os.setpriority(os.PRIO_PROCESS, 0, {nice})\n        "
        + _ioprio_call(IOPRIO_WHO_PROCESS, 0, IOPRIO_IDLE)
        + "\n        return 1.0, 0.0",
    )
    with started([source], tmp_path) as [car]:
        _reset(car)
        assert _step(car) == (1.0, 0.0)
        assert os.getpriority(os.PRIO_PROCESS, car.pid) == nice
        assert _ioprio(car.pid) == IOPRIO_IDLE


@pytest.mark.parametrize("where", ["import", "step"])
def test_a_car_cannot_start_a_thread(tmp_path: Path, where: str) -> None:
    """One thread per car: the car gets Python's ordinary error."""
    code = "import threading; threading.Thread(target=print).start()"
    with started([_in(where, code)], tmp_path) as [car]:
        fault = _fault_of(car, where)
    assert fault is not None
    assert "can't start new thread" in fault


def test_a_car_may_use_numpy_and_import_more_later(tmp_path: Path) -> None:
    """Imports the car's own file did not make, made from `step`, still
    read the libraries they need."""
    source = _in(
        "step",
        """import numpy as np
        import scipy.constants, xml.dom.minidom, colorsys
        x = float((np.eye(50) @ np.eye(50)).trace())
        return x + round(scipy.constants.pi) + colorsys.ONE_THIRD * 0, 0.0""",
    )
    with started([source], tmp_path) as [car]:
        _reset(car)
        assert _step(car) == (53.0, 0.0)


@pytest.mark.parametrize("where", ["import", "reset", "step"])
@pytest.mark.parametrize(
    "code",
    [
        "open({secret!r}).read()",
        "open({fresh!r}, 'w').write('x')",
        "os.listdir({folder!r})",
        "open('/etc/hostname').read()",
    ],
)
def test_a_car_can_neither_read_nor_write_a_file(
    tmp_path: Path, where: str, code: str
) -> None:
    """Not the event's state, not its own folder, nothing: only the
    libraries Python imports from."""
    secret = tmp_path / "state.json"
    secret.write_text("{}")
    fresh = tmp_path / "written"
    code = code.format(secret=str(secret), fresh=str(fresh), folder=str(tmp_path))
    with started([_in(where, code)], tmp_path) as [car]:
        fault = _fault_of(car, where)
    assert fault is not None
    assert "[Errno 13] Permission denied" in fault, fault
    assert not fresh.exists()


def test_a_car_cannot_open_a_siblings_proc_files(tmp_path: Path) -> None:
    with started([DRIVES], tmp_path) as [sibling]:
        snoop = _in("step", f"os.listdir('/proc/{sibling.pid}/fd')")
        with started([snoop], _elsewhere(tmp_path)) as [car]:
            assert _fault_of(car, "step") == (
                f"PermissionError: [Errno 13] Permission denied: "
                f"'/proc/{sibling.pid}/fd'"
            )


def test_a_car_cannot_read_another_teams_source(tmp_path: Path) -> None:
    """The file the other car's import names resolves, in this car, to its
    own source alone."""
    secret = DRIVES + "\n# SECRET of car0\n"
    snoop = _in(
        "step",
        f"""text = open({str(tmp_path / "car0.py")!r}).read()
        raise RuntimeError("found it" if "SEC" + "RET" in text else "not there")""",
    )
    with started([secret, snoop], tmp_path) as [_, car]:
        assert _fault_of(car, "step") == "RuntimeError: not there"


@pytest.mark.parametrize(
    ("part", "how"),
    [
        ("install", "raises"),
        ("restrict_files", "raises"),
        # What the unit's own syscall filter does to a call it refuses.
        ("install", "is killed"),
    ],
)
def test_a_car_that_cannot_install_its_filter_never_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    part: str,
    how: str,
) -> None:
    def broken(*args: object) -> None:
        if how == "is killed":
            os.kill(os.getpid(), signal.SIGSYS)
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(sandbox, part, broken)
    marker = tmp_path / "ran"
    source = _in("import", f"open({str(marker)!r}, 'w').close()")
    with started([source], tmp_path) as [car]:
        assert car.fault == car_process.SANDBOX_FAULT
    assert not marker.exists()
    # The admin reads the cause in the worker's stderr.
    err = capsys.readouterr().err
    assert "could not be sandboxed" in err
    assert ("SIGSYS" if how == "is killed" else "OSError") in err


# --- what the car's process is given -----------------------------------------


def test_a_cars_process_is_capped_and_first_for_the_oom_killer(
    tmp_path: Path,
) -> None:
    """RLIMIT_AS, soft and hard, is its size after the imports plus the
    budget: the size of the process it was forked from, give or take."""
    report = _in(
        "step",
        """import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        raise RuntimeError(f"{soft} {hard}")""",
    )
    with started([report], tmp_path) as [car]:
        size = car_process._address_space()
        # Read from here: the car itself may read no file.
        with open(f"/proc/{car.pid}/oom_score_adj") as f:
            oom = int(f.read())
        detail = _fault_of(car, "step")
    assert detail is not None
    soft, hard = map(int, detail.split(": ")[1].split())
    assert soft == hard
    budget = car_process.MEMORY_BUDGET_MB * 2**20
    assert abs(soft - size - budget) < 128 * 2**20
    assert oom == car_process.OOM_SCORE_ADJ == 1000


# --- replies the worker cannot read ------------------------------------------


@pytest.mark.parametrize(
    "forged",
    [
        "b'\\xff' * 8",  # a frame of 4 GB
        "struct.pack('>I', 2) + b'[]'",  # a frame, but not a reply
        "struct.pack('>I', 2) + b'{{'",  # not JSON at all
        "struct.pack('>I', 23) + b'{\"c\": [1.0, NaN], \"o\": []}'",
    ],
)
def test_a_car_that_forges_its_reply_is_dnf(tmp_path: Path, forged: str) -> None:
    source = _in("step", f"import struct; os.write(4, {forged}); time.sleep(5)")
    source = source.replace("import os,", "import time, os,")
    with started([source], tmp_path) as [car]:
        assert _fault_of(car, "step") == car_process.GARBLED_FAULT


# --- the average step budget -------------------------------------------------


@pytest.fixture
def short_window(monkeypatch: pytest.MonkeyPatch) -> int:
    """A window of 6 steps instead of 160, at the real 20 ms."""
    monkeypatch.setattr(car_process, "STEP_AVERAGE_WINDOW", 6)
    return 6


SLOW_STEPS = """
import time
from cocoracer.controller import Controller

class Car(Controller):
    def __init__(self):
        self.n = 0

    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        self.n += 1
        time.sleep({pause})
        return 1.0, 0.0
"""


@pytest.mark.parametrize(
    "pause",
    [
        "0.1",  # every step
        "0.05 if self.n % 2 else 0.0",  # every other step: 25 ms on average
    ],
)
def test_a_car_slow_on_average_is_dnf_once_its_window_is_full(
    tmp_path: Path, short_window: int, pause: str
) -> None:
    with started([SLOW_STEPS.format(pause=pause)], tmp_path) as [car]:
        _reset(car)
        for _ in range(short_window - 1):
            assert _step(car) == (1.0, 0.0)
        with pytest.raises(ControllerFault, match="en moyenne plus de 20 ms"):
            _step(car)
    assert car.fault == car_process.STEP_AVERAGE_FAULT


def test_one_slow_step_among_cheap_ones_is_not_dnf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At the real window, 0.9 s once is 5.6 ms on average; here, with a
    window of 20, 0.15 s once is 7.5 ms."""
    monkeypatch.setattr(car_process, "STEP_AVERAGE_WINDOW", 20)
    source = SLOW_STEPS.format(pause="0.15 if self.n == 3 else 0.0")
    with started([source], tmp_path) as [car]:
        _reset(car)
        for _ in range(40):
            assert _step(car) == (1.0, 0.0)
    assert car.fault is None


def test_the_average_fault_is_in_french_and_names_the_budget() -> None:
    assert car_process.STEP_AVERAGE_FAULT == (
        "le contrôleur a pris en moyenne plus de 20 ms par step"
    )
    assert car_process.STEP_AVERAGE_WINDOW == 160
    assert car_process.STEP_AVERAGE_LIMIT == 0.020


# --- the field, in parallel --------------------------------------------------


def test_the_cars_step_in_parallel(tmp_path: Path) -> None:
    """Four cars at 0.1 s a step cost a tick 0.1 s, not 0.4 s."""
    source = SLOW_STEPS.format(pause="0.1")
    with racing([source] * 4, tmp_path, steps=0) as (engine, _):
        started_at = time.monotonic()
        engine.tick()
        elapsed = time.monotonic() - started_at
    assert elapsed < 0.25
    assert faults(engine) == [None] * 4


STEERS = """
import numpy as np
from cocoracer.controller import Controller

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        left, right = laser_scan[18], laser_scan[54]
        return 4.0, float(np.clip(0.05 * (left - right), -0.4, 0.4))
"""


def test_a_race_comes_out_as_the_engine_runs_it_in_one_process(
    tmp_path: Path,
) -> None:
    """Parallel steps, commands applied in grid order: the same race, to
    the bit, as cocoracer's own engine stepping the same controllers."""
    sources = [STEERS, DRIVES, STEERS, SITS]
    with racing(sources, tmp_path, steps=80) as (engine, _):
        poses = [(v.x, v.y, v.yaw, v.speed) for v in engine.vehicles]
    config = load_config(DEFAULT_PARAMS)
    controllers: list[Controller] = []
    for i, source in enumerate(sources):
        path = tmp_path / f"local{i}.py"
        path.write_text(source)
        controllers.append(load_controller(path, baselines=config.baselines))
    names = [f"car{i}" for i in range(len(sources))]
    local = RaceEngine(mini_track(), config, controllers, names, "race")
    for _ in range(COUNTDOWN_TICKS + 80):
        local.tick()
    assert poses == [(v.x, v.y, v.yaw, v.speed) for v in local.vehicles]


def test_a_car_that_would_not_load_waits_on_the_grid_until_its_first_step(
    tmp_path: Path,
) -> None:
    """Like a controller that raises in `step`: through the wait and the
    countdown it is on the grid with the rest of the field, and it DNFs on
    the first racing tick, with the reason it would not load."""
    with on_the_grid([DRIVES, "this is not python\n"], tmp_path) as (engine, _):
        assert engine.phase == "waiting"
        assert statuses(engine) == ["racing", "racing"]
        engine.start()
        for _ in range(COUNTDOWN_TICKS):
            engine.tick()
            assert statuses(engine) == ["racing", "racing"]
        assert faults(engine) == [None, None]
        engine.tick()
        assert statuses(engine) == ["racing", "dnf"]
        reason = faults(engine)[1]
    assert reason is not None
    assert reason.startswith(f"{car_process.LOAD_FAULT} : ")


def test_a_dnf_car_in_a_race_leaves_the_field_racing_and_no_process(
    tmp_path: Path,
) -> None:
    exits = _in("step", "sys.exit(1)")
    with racing([DRIVES, exits, DRIVES], tmp_path) as (engine, cars):
        assert statuses(engine) == ["racing", "dnf", "racing"]
        assert faults(engine) == [None, f"{STOPPED} (code de sortie 1)", None]
    for car in cars:
        with pytest.raises(ChildProcessError):
            os.waitpid(car.pid, os.WNOHANG)
