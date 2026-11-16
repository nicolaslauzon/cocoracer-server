"""Racing in a killable child process.

These spawn a real subprocess, so they cost roughly a second each — but the
behaviour under test is precisely what happens across a process boundary,
and a fake would not exercise it. Each race is capped at a few simulated
seconds: every entrant is classified whether it finishes or times out, so
the classification these assert on does not need a race run to its end.
"""

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from cocoracer.config import load_config
from cocoracer.race_state import VehicleStatus

from cocoracer_server import runner
from cocoracer_server.car_field import start_cars
from cocoracer_server.race_worker import Recorder
from cocoracer_server.runner import Entry, run_race_in_subprocess

DRIVES = """
from cocoracer.controller import Controller

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        return 6.0, 0.0
"""

RAISES = """
from cocoracer.controller import Controller

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        raise IndexError("boom")
"""

# A hang that swallows every exception, as the old in-process timer's
# interrupt was one: the step's deadline on the car's pipe ends it anyway.
HANGS_FOR_GOOD = """
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

# Long enough to separate a car that drives from one that never moves,
# short enough that the race is over in about a second.
_RACE_SECONDS = 3.0

# Three ways a debugging student ends up on the descriptor the parent parses
# the result from: at import, every step, and straight down `sys.stdout`.
PRINTS = """
import sys
from cocoracer.controller import Controller

print("noise from the module body")

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        print("noise from step", flush=True)
        sys.stdout.write("more noise\\n")
        sys.stdout.flush()
        return 6.0, 0.0
"""

SYNTAX_ERROR = "class Car(Controller:\n    oops\n"

NO_CONTROLLER = "x = 1\n"

# The replay the player is developed against (`data/make_replay_fixture.py`).
FIXTURE = json.loads(
    (Path(__file__).parent / "data" / "replay_fixture.json").read_text(encoding="utf-8")
)

# A replay row's columns, in `Recorder.COLUMNS` order, with their types.
_ROW: tuple[tuple[str, type | tuple[type, ...]], ...] = tuple(
    zip(
        Recorder.COLUMNS,
        (float, float, float, float, int, str, int, float, (float, type(None))),
        strict=True,
    )
)


def _assert_shaped_like_the_fixture(replay: dict[str, object]) -> None:
    """`replay` has the fixture's keys, frame keys and row columns, and
    only statuses the player knows (`TestStatusLabels` in test_views)."""
    assert replay.keys() == FIXTURE.keys()
    statuses = {s.value for s in VehicleStatus}
    for recording in (replay, FIXTURE):
        frames = recording["frames"]
        assert isinstance(frames, list)
        for frame in frames:
            assert frame.keys() == FIXTURE["frames"][0].keys()
            for row in frame["cars"]:
                assert len(row) == len(_ROW)
                for value, (column, kind) in zip(row, _ROW, strict=True):
                    assert isinstance(value, kind), (column, value)
                assert row[5] in statuses


def test_the_replay_player_indexes_rows_in_the_recorders_column_order() -> None:
    script = (Path(runner.__file__).parent / "static" / "replay.js").read_text(
        encoding="utf-8"
    )
    match = re.search(r"const COL = \{(.*?)\};", script, re.S)
    assert match is not None
    indices = {name: int(i) for name, i in re.findall(r"(\w+): (\d+)", match.group(1))}
    assert indices == {name: i for i, name in enumerate(Recorder.COLUMNS)}
    # Every row index the player reads goes through `COL`.
    assert re.findall(r"car\[(?!COL\.)", script) == []


def _tower(replay: dict[str, list]) -> tuple[str, ...]:
    """The final frame's field in the replay tower's order: finishers by
    finish time, then the rest by distance, the classification's rule."""
    final = replay["frames"][-1]["cars"]

    def key(c: int) -> tuple[int, float]:
        status, distance, finish_time = final[c][5], final[c][7], final[c][8]
        if status == "finished":
            return (0, finish_time)
        return (1, -distance)

    return tuple(replay["names"][c] for c in sorted(range(len(final)), key=key))


def test_a_normal_race_classifies_every_entrant_and_records_it() -> None:
    entries = [Entry("alpha", DRIVES), Entry("bravo", DRIVES)]
    outcome = run_race_in_subprocess(
        entries, "stadium", laps=1, time_limit=_RACE_SECONDS
    )
    assert not outcome.void
    assert set(outcome.classified) == {"alpha", "bravo"}
    assert len(outcome.classified) == 2
    assert outcome.replay is not None
    replay = json.loads(outcome.replay)
    assert replay["names"] == ["alpha", "bravo"]
    assert replay["hz"] == 10
    assert replay["static"]["type"] == "static"
    frames = replay["frames"]
    times = [frame["t"] for frame in frames]
    assert times == sorted(set(times))
    assert times[0] == 0.0 and frames[0]["phase"] == "countdown"
    assert times[1] == pytest.approx(0.1)
    _assert_shaped_like_the_fixture(replay)
    # The final frame is the race's end, and agrees with its classification.
    final = frames[-1]
    assert final["t"] == pytest.approx(_RACE_SECONDS, abs=0.05)
    assert _tower(replay) == outcome.classified
    # Passes are counted from the distances at the release and the end,
    # the replay's first and last frames.
    assert outcome.lap_length is not None and outcome.lap_length > 0
    for c, name in enumerate(replay["names"]):
        start, end = outcome.distances[name]
        assert start == pytest.approx(frames[0]["cars"][c][7], abs=0.01)
        assert end == pytest.approx(final["cars"][c][7], abs=0.01)
    for name, (_, _, _, _, laps, status, crashes, _, _) in zip(
        replay["names"], final["cars"], strict=True
    ):
        assert status == outcome.statuses[name]
        assert isinstance(laps, int) and isinstance(crashes, int)
        if status == "finished":
            assert laps == 1


def test_a_replay_the_size_of_a_long_race_crosses_the_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 16-car race run to the official five-minute limit is about 2.5 MB;
    a stand-in child writes twice that, to prove the pipe is not the limit."""
    frame = {
        "t": 0.0,
        "phase": "racing",
        "countdown": 0.0,
        "cars": [[123.45, -67.89, 3.14, 15.0, 2, "racing", 1]] * 16,
    }
    child = (
        "import json, sys\n"
        "sys.stdin.read()\n"
        f"frame = {frame!r}\n"
        "json.dump({'classified': ['alpha'], 'statuses': {'alpha': 'finished'},"
        " 'faults': {}, 'distances': {'alpha': [0, 1]}, 'lap_length': 100,"
        " 'replay': {'frames': [frame] * 6000}}, sys.stdout)\n"
    )
    monkeypatch.setattr(runner, "WORKER_ARGS", ("-c", child))
    outcome = run_race_in_subprocess(
        [Entry("alpha", DRIVES)], "stadium", laps=3, time_limit=_RACE_SECONDS
    )
    assert not outcome.void, outcome.void_reason
    assert outcome.replay is not None
    assert len(json.loads(outcome.replay)["frames"]) == 6000
    assert len(outcome.replay) > 4_000_000


def test_a_hanging_controller_faults_alone() -> None:
    """A hang in a submission costs that team its race, and only that team:
    the worker kills its car's process at the step's deadline, even one that
    swallows every exception."""
    outcome = run_race_in_subprocess(
        [Entry("alpha", DRIVES), Entry("stuck", HANGS_FOR_GOOD)],
        "stadium",
        laps=1,
        time_limit=_RACE_SECONDS,
    )
    assert not outcome.void, outcome.void_reason
    assert outcome.classified[-1] == "stuck"
    assert outcome.statuses["stuck"] == "dnf"
    assert outcome.faults["stuck"] == "le contrôleur n'a pas répondu en 1 s"
    assert "alpha" not in outcome.faults


def test_a_hanging_worker_is_killed_and_the_race_is_voided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The event must survive a race worker that never finishes, which no
    controller can make it do any more: a stand-in worker hangs instead."""
    child = "import sys, time\nsys.stdin.read()\ntime.sleep(60)\n"
    monkeypatch.setattr(runner, "WORKER_ARGS", ("-c", child))
    started = time.monotonic()
    outcome = run_race_in_subprocess(
        [Entry("alpha", DRIVES)], "stadium", laps=1, time_limit=0.5, grace=1.0
    )
    elapsed = time.monotonic() - started
    assert outcome.void
    assert outcome.void_reason == "arrêtée après 2 s : trop en retard sur le temps réel"
    assert outcome.replay is None
    # Killed on the budget, not left to run.
    assert elapsed < 10.0


def test_a_race_waiting_for_play_starts_on_it_and_not_on_the_budget() -> None:
    """The budget is counted from Play: here the wait outlasts it, and the
    race still runs, after Play and not before."""
    play = threading.Event()
    delay = 2.5
    pressed = threading.Timer(delay, play.set)
    started = time.monotonic()
    pressed.start()
    try:
        outcome = run_race_in_subprocess(
            [Entry("alpha", DRIVES)],
            "stadium",
            laps=1,
            time_limit=1.0,
            grace=1.0,
            play=play,
        )
    finally:
        pressed.cancel()
    elapsed = time.monotonic() - started
    assert not outcome.void, outcome.void_reason
    assert outcome.classified == ("alpha",)
    # The worker was still waiting when Play came; a race that had started
    # by itself would have ended, and returned, well before.
    assert elapsed >= delay
    assert outcome.replay is not None
    first = json.loads(outcome.replay)["frames"][0]
    assert first["t"] == 0.0 and first["phase"] == "countdown"


def test_a_race_waiting_for_play_ends_if_its_worker_dies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Play that never comes cannot hold a dead worker's race forever."""
    child = "import sys\nsys.stdin.readline()\nsys.exit('worker broke')\n"
    monkeypatch.setattr(runner, "WORKER_ARGS", ("-c", child))
    outcome = run_race_in_subprocess(
        [Entry("alpha", DRIVES)],
        "stadium",
        laps=1,
        time_limit=_RACE_SECONDS,
        play=threading.Event(),
    )
    assert outcome.void
    assert outcome.void_reason == "le processus de course a échoué : worker broke"


def test_a_worker_whose_runner_is_gone_never_races_alone(tmp_path: Path) -> None:
    """Stdin closed before Play means the server that would press it is
    gone: the worker exits rather than run the race on its own."""
    from cocoracer_server import race_worker

    request = {
        "track": "stadium",
        "laps": 1,
        "time_limit": 1.0,
        "workdir": str(tmp_path),
        "live": False,
        "wait_for_play": True,
        "entries": [{"name": "alpha"}],
    }
    done = subprocess.run(
        [sys.executable, *runner.WORKER_ARGS],
        input=json.dumps(request) + "\n" + json.dumps(DRIVES) + "\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == race_worker.NO_PLAY_EXIT, done.stderr
    assert done.stdout == ""
    assert "stdin closed before Play" in done.stderr


def test_a_raising_controller_does_not_void_the_race() -> None:
    entries = [Entry("alpha", DRIVES), Entry("broken", RAISES)]
    outcome = run_race_in_subprocess(
        entries, "stadium", laps=1, time_limit=_RACE_SECONDS
    )
    assert not outcome.void
    assert outcome.classified[-1] == "broken"
    assert "IndexError" in outcome.faults["broken"]


@pytest.mark.parametrize("source", [SYNTAX_ERROR, NO_CONTROLLER])
def test_a_controller_that_will_not_load_keeps_its_grid_slot(source: str) -> None:
    """It is classified last rather than dropped, because the other cars'
    positions gained are measured against the grid they started on."""
    entries = [Entry("alpha", DRIVES), Entry("junk", source)]
    outcome = run_race_in_subprocess(
        entries, "stadium", laps=1, time_limit=_RACE_SECONDS
    )
    assert not outcome.void
    assert set(outcome.classified) == {"alpha", "junk"}
    assert outcome.classified[-1] == "junk"
    assert outcome.faults["junk"].startswith("le contrôleur n'a pas pu être chargé : ")


def test_a_printing_controller_does_not_void_the_race() -> None:
    """`print()` while debugging must not cost the whole field its race;
    what it prints is its car's log."""
    entries = [Entry("alpha", DRIVES), Entry("chatty", PRINTS)]
    outcome = run_race_in_subprocess(
        entries, "stadium", laps=1, time_limit=_RACE_SECONDS
    )
    assert not outcome.void, outcome.void_reason
    assert set(outcome.classified) == {"alpha", "chatty"}
    assert "chatty" not in outcome.faults
    # And it comes back as the car's log.
    assert set(outcome.logs) == {"chatty"}
    lines = outcome.logs["chatty"]["lines"]
    assert lines[0] == [0.0, "noise from the module body"]
    assert lines[1][1] == "noise from step" and lines[1][0] > 0
    assert lines[2][1] == "more noise"


@pytest.mark.parametrize(
    ("die", "reason"),
    [
        (
            "os.kill(os.getpid(), signal.SIGKILL)",
            "le processus de course a manqué de mémoire (tué par le système)",
        ),
        (
            "os.kill(os.getpid(), signal.SIGSEGV)",
            "le processus de course a été tué par le signal SIGSEGV",
        ),
        ("sys.exit('worker broke')", "le processus de course a échoué : worker broke"),
        # A traceback: its last line, the exception, is the cause.
        (
            "raise RuntimeError('no track')",
            "le processus de course a échoué : RuntimeError: no track",
        ),
        ("sys.exit(4)", "le processus de course a échoué (code de sortie 4)"),
        (
            "sys.exit(0)",
            "le processus de course s'est terminé sans renvoyer de résultat",
        ),
    ],
)
def test_a_worker_that_dies_without_a_result_is_voided_with_the_cause(
    monkeypatch: pytest.MonkeyPatch, die: str, reason: str
) -> None:
    """A SIGKILL the runner did not send is the OOM killer's; any other
    signal is named, so the void says what happened to the race."""
    child = f"import os, signal, sys\nsys.stdin.read()\n{die}\n"
    monkeypatch.setattr(runner, "WORKER_ARGS", ("-c", child))
    outcome = run_race_in_subprocess(
        [Entry("alpha", DRIVES)], "stadium", laps=1, time_limit=_RACE_SECONDS
    )
    assert outcome.void
    assert outcome.void_reason == reason
    assert outcome.replay is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_the_worker_volunteers_for_the_oom_killer_after_its_cars() -> None:
    """When the unit runs out of memory the kernel must pick a car first
    (1000, `test_car_process`), then the race worker, and never the server
    that shares their cgroup (0)."""
    from cocoracer_server import race_worker

    done = subprocess.run(
        [
            sys.executable,
            "-c",
            "from cocoracer_server import race_worker as w\n"
            "w.volunteer_for_the_oom_killer(w.OOM_SCORE_ADJ)\n"
            "print(open('/proc/self/oom_score_adj').read())",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    assert int(done.stdout) == race_worker.OOM_SCORE_ADJ == 500


def _hostile(code: str) -> str:
    return f"""
import os, signal, socket, subprocess, sys
from cocoracer.controller import Controller

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        {code}
        return 6.0, 0.0
"""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_no_controller_can_end_the_race_for_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each of these used to stop the worker, and the race with it, or to
    reach out of it. Now each costs its own car alone (ADR 0007)."""
    monkeypatch.setenv("COCORACER_ADMIN_PASSWORD", "hunter2")
    refused = "PermissionError: [Errno 1] Operation not permitted"
    stopped = "le processus du contrôleur s'est arrêté"
    hostile = {
        "exits": ("sys.exit(1)", f"{stopped} (code de sortie 1)"),
        "_exits": ("os._exit(1)", f"{stopped} (code de sortie 1)"),
        "kills itself": (
            "os.kill(os.getpid(), signal.SIGKILL)",
            f"{stopped} (tué par le signal SIGKILL)",
        ),
        "kills the worker": ("os.kill(os.getppid(), signal.SIGKILL)", refused),
        "kills the group": ("os.kill(0, signal.SIGKILL)", refused),
        "opens a socket": ("socket.create_connection(('127.0.0.1', 9))", refused),
        "starts a process": ("subprocess.run(['true'])", refused),
        "steals the alarm": (
            "signal.signal(signal.SIGALRM, signal.SIG_IGN)\n        while True: pass",
            "le contrôleur n'a pas répondu en 1 s",
        ),
        "reads the password": (
            "raise RuntimeError(os.environ.get('COCORACER_ADMIN_PASSWORD'))",
            "RuntimeError: None",
        ),
    }
    entries = [Entry("alpha", DRIVES)] + [
        Entry(name, _hostile(code)) for name, (code, _) in hostile.items()
    ]
    outcome = run_race_in_subprocess(entries, "stadium", laps=1, time_limit=2.0)
    assert not outcome.void, outcome.void_reason
    assert set(outcome.classified) == {e.name for e in entries}
    assert "alpha" not in outcome.faults
    for name, (_, reason) in hostile.items():
        assert outcome.statuses[name] == "dnf"
        assert outcome.faults[name].startswith(reason), (name, outcome.faults[name])


def _gone(pid: int, within: float = 2.0) -> bool:
    """Whether `pid` is dead (or a zombie awaiting its reaper) within `within` s."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            with open(f"/proc/{pid}/stat") as f:
                state = f.read().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            return True
        if state in ("Z", "X"):
            return True
        time.sleep(0.05)
    return False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.parametrize("ending", ["normal", "timeout"])
def test_no_process_a_controller_spawned_outlives_the_race(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ending: str
) -> None:
    """A stand-in worker starts a `sleep` the way a controller would, one
    that inherits its pipes, then either finishes or hangs. Either way the
    `sleep` is killed with the worker's group, and a normal end is not
    mistaken for a hang because the `sleep` holds the pipes open."""
    pid_file = tmp_path / "sleep.pid"
    finish = (
        "json.dump({'classified': ['alpha'], 'statuses': {'alpha': 'finished'},"
        " 'faults': {}, 'distances': {'alpha': [0, 1]}, 'lap_length': 100,"
        " 'replay': {}}, sys.stdout)\n"
        if ending == "normal"
        else "time.sleep(60)\n"
    )
    child = (
        "import json, subprocess, sys, time\n"
        "sys.stdin.read()\n"
        "sleeper = subprocess.Popen(['sleep', '60'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(sleeper.pid))\n" + finish
    )
    monkeypatch.setattr(runner, "WORKER_ARGS", ("-c", child))
    started = time.monotonic()
    outcome = run_race_in_subprocess(
        [Entry("alpha", DRIVES)], "stadium", laps=1, time_limit=0.1, grace=1.5
    )
    elapsed = time.monotonic() - started
    assert outcome.void == (ending == "timeout"), outcome.void_reason
    if ending == "normal":
        assert elapsed < 1.5  # done when the worker exits, not on the budget
    assert _gone(int(pid_file.read_text()))


def test_the_sweep_runs_only_inside_the_units_own_cgroup() -> None:
    unit = "0::/system.slice/cocoracer-server.service\n"
    desktop = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/x.scope\n"
    systemd = {"INVOCATION_ID": "abc"}
    assert runner.unit_procs_file({}, unit) is None
    assert runner.unit_procs_file(systemd, desktop) is None
    assert runner.unit_procs_file(systemd, "0::/system.slice/other.service\n") is None
    assert runner.unit_procs_file(systemd, unit) == Path(
        "/sys/fs/cgroup/system.slice/cocoracer-server.service/cgroup.procs"
    )


def test_the_sweep_is_a_no_op_outside_systemd(monkeypatch: pytest.MonkeyPatch) -> None:
    """This test run is not the deployed unit, so nothing may be killed."""

    def refuse(pid: int, sig: int) -> None:
        raise AssertionError(f"the sweep tried to kill {pid}")

    monkeypatch.setattr(runner.os, "kill", refuse)
    assert runner.unit_procs_file() is None
    assert runner.sweep_unit() == 0


def test_the_sweep_never_targets_the_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert runner.pids_to_sweep("12\n4242\n13\n", server_pid=4242) == [12, 13]
    procs = tmp_path / "cgroup.procs"
    procs.write_text(f"{os.getpid()}\n999991\n999992\n")
    killed: list[int] = []

    def record(pid: int, sig: int) -> None:
        if pid == 999992:
            raise ProcessLookupError  # exited on its own meanwhile
        killed.append(pid)

    monkeypatch.setattr(runner.os, "kill", record)
    assert runner.sweep_unit(procs) == 1
    assert killed == [999991]


def test_an_empty_field_is_voided_without_spawning_anything() -> None:
    outcome = run_race_in_subprocess([], "stadium", laps=1, time_limit=_RACE_SECONDS)
    assert outcome.void
    assert outcome.void_reason == "aucune équipe au départ"


class _Abandoned(Exception):
    """Raised from Play to leave the race on the grid once a test is done."""


def test_a_viewer_on_the_lan_cannot_start_the_live_race(tmp_path: Path) -> None:
    """Teams watch from their own machines, so the view answers on every
    interface; and it is read-only: a start a viewer sends leaves the field
    on the grid, where only the admin's Play releases it."""
    import websockets.sync.client

    from cocoracer_server import race_worker

    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        port = int(s.getsockname()[1])
    request = {
        "track": "stadium",
        "laps": 1,
        "time_limit": 1.0,
        "workdir": str(tmp_path),
        "live": True,
        "port": port,
        "hold": 0.0,
        "wait_for_play": True,
        "entries": [{"name": "alpha"}],
    }
    phases: list[str] = []

    def play() -> None:
        # Loopback, but not 127.0.0.1: only a view bound to every interface
        # answers here.
        url = f"ws://127.0.0.2:{port}/ws"
        with websockets.sync.client.connect(url, max_size=None) as ws:
            ws.recv()  # the static message
            ws.send(json.dumps({"type": "start"}))
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                phases.append(json.loads(ws.recv())["phase"])
        raise _Abandoned

    with pytest.raises(_Abandoned):
        race_worker.run(request, [DRIVES], play)
    assert phases
    assert set(phases) == {"waiting"}


SITS = """
from cocoracer.controller import Controller

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        return 0.0, 0.0
"""


def test_a_lapless_race_ends_at_its_limit_classified_by_distance() -> None:
    """The qualification's shape: no lap target, so the cars still out at the
    limit are done, not DNF, and the one that went further ranks first even
    from behind on the grid."""
    entries = [Entry("sitter", SITS), Entry("driver", DRIVES)]
    outcome = run_race_in_subprocess(
        entries, "stadium", laps=None, time_limit=_RACE_SECONDS + 2.0
    )
    assert not outcome.void
    assert outcome.classified == ("driver", "sitter")
    assert outcome.statuses == {"driver": "done", "sitter": "done"}
    # The replay's tower agrees, from the distance the engine ranked by.
    assert outcome.replay is not None
    assert _tower(json.loads(outcome.replay)) == outcome.classified


def test_a_live_race_without_play_waits_its_preroll_then_starts_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live qualification holds the field on the grid for its pre-roll,
    counted from the view being up, so a viewer who joins then sees the
    whole countdown; then it starts on its own, with no Play."""
    import websockets.sync.client

    from cocoracer_server import race_worker

    # A live race runs at wall-clock pace: a quarter-second countdown, not
    # the real three, keeps this test about a second long.
    def short_countdown(params: object) -> Any:
        config = load_config(params)
        config.race.countdown = 0.25
        return config

    monkeypatch.setattr(race_worker, "load_config", short_countdown)

    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        port = int(s.getsockname()[1])
    preroll = 0.5
    request = {
        "track": "stadium",
        "laps": 1,
        "time_limit": 1.0,
        "workdir": str(tmp_path),
        "live": True,
        "port": port,
        "hold": 0.0,
        "preroll": preroll,
        "wait_for_play": False,
        "entries": [{"name": "alpha"}],
    }
    seen: list[tuple[float, str]] = []

    def watch() -> None:
        # The viewer who is there the moment the socket comes up.
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            try:
                ws = websockets.sync.client.connect(f"ws://127.0.0.1:{port}/ws")
            except OSError:
                time.sleep(0.01)
                continue
            with ws:
                ws.recv()  # the static message
                try:
                    while True:
                        phase = json.loads(ws.recv())["phase"]
                        seen.append((time.monotonic(), phase))
                except websockets.exceptions.ConnectionClosed:
                    return

    # The car is forked before the viewer's thread exists.
    cars = start_cars(["alpha"])
    viewer = threading.Thread(target=watch, daemon=True)
    viewer.start()
    result = race_worker.run(request, [DRIVES], cars=cars)
    viewer.join(5.0)
    assert result["classified"] == ["alpha"]
    phases = [phase for _, phase in seen]
    assert phases[0] == "waiting"
    assert "countdown" in phases
    first_countdown = seen[phases.index("countdown")][0]
    assert first_countdown - seen[0][0] >= preroll - 0.2
    # The replay still opens at the release.
    first = result["replay"]["frames"][0]
    assert first["t"] == 0.0 and first["phase"] == "countdown"


def test_a_live_races_preroll_is_inside_its_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stand-in worker spends the pre-roll the request names, on top of a
    race that fits its budget: waiting on the grid voids nothing."""
    child = (
        "import json, sys, time\n"
        "request = json.loads(sys.stdin.readline())\n"
        "time.sleep(request['preroll'])\n"
        "print(json.dumps({'classified': ['alpha'], 'statuses': {},"
        " 'faults': {}, 'distances': {'alpha': [0, 1]}, 'lap_length': 100,"
        " 'replay': {}}))\n"
    )
    monkeypatch.setattr(runner, "WORKER_ARGS", ("-c", child))
    outcome = run_race_in_subprocess(
        [Entry("alpha", DRIVES)],
        "stadium",
        laps=1,
        time_limit=0.1,
        grace=0.3,
        live=True,
        preroll=0.6,
    )
    assert not outcome.void, outcome.void_reason
    assert outcome.classified == ("alpha",)
