"""Run one race in a process the server can kill.

The engine survives a controller that raises, but not one that never
returns. A 40 Hz race loop calling a player's `while True:` blocks forever,
and with it the whole event schedule. So every race is a child process with
a wall-clock budget: if the budget passes, the parent kills it and the race
is recorded as void rather than blocking the afternoon.

The child's result carries the race's replay, a few MB of JSON for a
three-lap race, back over the same pipe; `communicate` drains it as it
comes, so the size costs memory and nothing else. The replay is serialised
again here, once, and handed on as bytes: the store keeps or writes those,
and the server serves them as they are.

No process outlives its race (ADR 0004). The worker leads its own process
group, and every race end, whether normal, timed out or failed, kills that
whole group; under the deployed unit a sweep of the unit's cgroup then kills
anything that left the group with `setsid`.

Every car runs in a process of its own under the worker (ADR 0007), so
what a controller does voids no race: a void is the server's own failure,
or the race falling behind real time.
"""

import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

DEFAULT_GRACE = 20.0
RACE_VIEW_PORT = 8000
DEFAULT_HOLD = 2.0
# Seconds a live race that waits for no Play (a qualification) holds the
# field on the grid before its countdown, counted from its view accepting
# connections, so the viewers who join as it comes up see the whole start.
PREROLL = 2.0

# The line on the worker's stdin that starts a race waiting for Play, and
# how often, in seconds, the wait for it checks the worker is still alive.
PLAY = "PLAY"
PLAY_POLL = 0.2

# The void reason for a race with nobody on the grid.
NO_ENTRIES = "aucune équipe au départ"

# What the child runs, after the interpreter. A module-level name so a test
# can stand a cheaper child in its place to exercise the pipe on its own.
WORKER_ARGS: tuple[str, ...] = ("-m", "cocoracer_server.race_worker")

# Environment variables the worker, and so every car, is started without.
WITHHELD_FROM_WORKER = frozenset({"COCORACER_ADMIN_PASSWORD"})

# How much of the worker's stderr, its tail, reaches the server's.
RELAYED_STDERR_CHARS = 16 * 1024

# The systemd unit `deploy.sh` installs (its SERVICE, plus ".service"). The
# sweep only ever runs inside this unit's own cgroup.
UNIT_NAME = "cocoracer-server.service"

# Seconds to wait, once the race is over and its processes killed, for the
# worker's output to reach end-of-file. Only a process that escaped both the
# group kill and the sweep (so: not under the unit) can hold it open longer,
# and by then the worker has written everything it will.
DRAIN_TIMEOUT = 5.0


@dataclass(frozen=True)
class Entry:
    """One car on the grid: a team and the source it is racing."""

    name: str
    source: str


@dataclass(frozen=True)
class RaceOutcome:
    """What came back from a race, or the fact that nothing did.

    `replay` is the race's recording in the player's format, serialised to
    JSON, or None for a void race, which has nothing to replay. `logs` is
    what each car printed, by team, in the worker's format; a void race's
    is lost with the worker. A void's `void_reason` is in French: the race
    page shows it to the teams. `distances` is each car's distance at the
    release and at the end, and `lap_length` the track's, which passes are
    counted from (`scoring.passes`); a void race has neither.
    """

    classified: tuple[str, ...] = ()
    statuses: dict[str, str] = field(default_factory=dict)
    faults: dict[str, str] = field(default_factory=dict)
    replay: bytes | None = None
    logs: dict[str, Any] = field(default_factory=dict)
    distances: dict[str, tuple[float, float]] = field(default_factory=dict)
    lap_length: float | None = None
    void: bool = False
    void_reason: str | None = None

    @classmethod
    def voided(cls, reason: str) -> "RaceOutcome":
        return cls(void=True, void_reason=reason)


def _failure_reason(returncode: int, err: str) -> str:
    """Why a worker that exited without a result failed, for the void.

    In French, like every void reason, because the race page shows it to the
    teams; the worker's own words are quoted as they came: the last line it
    wrote on stderr, which for a Python traceback is the exception, or else
    its exit code.

    A negative return code is the signal that killed it. `SIGKILL` from
    anyone but the timeout above is the kernel's OOM killer, or systemd's
    when the unit hits `MemoryMax`.
    """
    if returncode < 0:
        try:
            name = signal.Signals(-returncode).name
        except ValueError:
            name = f"signal {-returncode}"
        if name == "SIGKILL":
            return "le processus de course a manqué de mémoire (tué par le système)"
        return f"le processus de course a été tué par le signal {name}"
    said = [line.strip() for line in err.splitlines() if line.strip()]
    if said:
        return f"le processus de course a échoué : {said[-1][:500]}"
    return f"le processus de course a échoué (code de sortie {returncode})"


def worker_environment() -> dict[str, str]:
    """The server's environment, less what team code must not read.

    The cars are the worker's children and inherit it, and a controller can
    read `os.environ`: the admin password, which the deployed unit hands
    the server in its environment, stays with the server.
    """
    return {k: v for k, v in os.environ.items() if k not in WITHHELD_FROM_WORKER}


def _relay(err: str) -> None:
    """Pass what the worker wrote on stderr on to the server's own, so the
    admin finds it in the service's journal: a car that could not be
    sandboxed, a warning. The cars' own output never gets there."""
    err = err.strip()
    if err:
        print(err[-RELAYED_STDERR_CHARS:], file=sys.stderr, flush=True)


def run_race_in_subprocess(
    entries: list[Entry],
    track: str,
    laps: int | None,
    time_limit: float,
    grace: float = DEFAULT_GRACE,
    live: bool = False,
    port: int = RACE_VIEW_PORT,
    hold: float = DEFAULT_HOLD,
    after_winner: float | None = None,
    play: threading.Event | None = None,
    preroll: float = PREROLL,
) -> RaceOutcome:
    """Race `entries` in a child process, killing it if it overruns.

    `laps` None races to the time limit and classifies by distance;
    `after_winner` ends the race that many seconds after the first finish
    (cocoracer's `race.laps` and `race.after_winner`).
    `time_limit` is the race's own simulated-time limit. Headless, the child
    simulates far faster than real time; `live` paces it to the wall clock
    and serves the live view on `port` of every interface, so the budget has to cover the race
    actually being watched, plus `grace` for the countdown and startup.
    `hold` is how long the child leaves the final frame up in the live view
    before it exits, and is spent inside the budget like everything else.

    With `play`, the field waits on the grid, live, until `play` is set,
    however long that takes, and only then does the countdown run. The
    budget starts at the press, not at the spawn, so the wait cannot void
    the race. Live without `play`, the field waits `preroll` seconds on the
    grid once the view is up, then starts by itself; the budget's grace
    grows by as much, so the wait cannot void it either.
    """
    if not entries:
        return RaceOutcome.voided(NO_ENTRIES)
    if live and play is None:
        grace += preroll
    budget = time_limit + grace
    with tempfile.TemporaryDirectory(prefix="cocoracer-race-") as workdir:
        request = {
            "track": track,
            "laps": laps,
            "time_limit": time_limit,
            "after_winner": after_winner,
            "workdir": workdir,
            "live": live,
            "port": port,
            "hold": hold,
            "preroll": preroll,
            "wait_for_play": play is not None,
            "entries": [{"name": e.name} for e in entries],
        }
        # The sources follow the request, one line each, so that the worker
        # can fork every car's process before any of them is in its memory.
        lines = [json.dumps(request), *(json.dumps(e.source) for e in entries)]
        process = subprocess.Popen(
            [sys.executable, *WORKER_ARGS],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=worker_environment(),
            # Its own process group, so every process a controller starts
            # can be killed with it (see `_end_the_race`).
            start_new_session=True,
        )
        out, err, timed_out = _run_worker(process, "\n".join(lines), budget, play)
        _relay(err)
        if timed_out:
            return RaceOutcome.voided(
                f"arrêtée après {budget:.0f} s : trop en retard sur le temps réel"
            )
        if process.returncode != 0:
            return RaceOutcome.voided(_failure_reason(process.returncode, err))
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            if not out.strip():
                return RaceOutcome.voided(
                    "le processus de course s'est terminé sans renvoyer de résultat"
                )
            return RaceOutcome.voided(
                f"le processus de course n'a renvoyé aucun résultat : {out[:200]}"
            )
    return RaceOutcome(
        classified=tuple(payload["classified"]),
        statuses=payload["statuses"],
        faults=payload["faults"],
        replay=json.dumps(payload["replay"], separators=(",", ":")).encode(),
        logs=payload.get("logs", {}),
        distances={
            name: (float(start), float(end))
            for name, (start, end) in payload["distances"].items()
        },
        lap_length=float(payload["lap_length"]),
    )


def _run_worker(
    process: subprocess.Popen[str],
    request: str,
    budget: float,
    play: threading.Event | None = None,
) -> tuple[str, str, bool]:
    """Feed the worker its request and collect its output and stderr.

    The request goes on stdin as it is given, lines of JSON, and a last
    newline. With `play`, stdin stays open until
    `play` is set (or the worker exits first), then carries the line
    `PLAY` and closes; the budget is counted from there.

    Returns `(stdout, stderr, timed_out)`. The race is over when the worker
    exits, not when its pipes close: a process a controller spawned inherits
    them and could hold them open for as long as it lives. So the pipes are
    drained on threads, the worker is waited on with the budget, and only
    once its processes are killed is the output read to the end.
    """
    assert process.stdin and process.stdout and process.stderr
    out: list[str] = []
    err: list[str] = []
    readers = [
        threading.Thread(target=_drain, args=(process.stdout, out), daemon=True),
        threading.Thread(target=_drain, args=(process.stderr, err), daemon=True),
    ]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        try:
            process.stdin.write(request + "\n")
            process.stdin.flush()
            if play is not None:
                _wait_for_play(process, play)
                process.stdin.write(PLAY + "\n")
            process.stdin.close()
        except BrokenPipeError:
            pass  # the worker died before reading it; its exit code says why
        timed_out = not _wait_unreaped(process, budget)
    finally:
        _end_the_race(process)
    for reader in readers:
        reader.join(DRAIN_TIMEOUT)
    return "".join(out), "".join(err), timed_out


def _wait_for_play(process: subprocess.Popen[str], play: threading.Event) -> None:
    """Wait for `play`, for as long as it takes, unless the worker exits."""
    while not play.is_set():
        if _wait_unreaped(process, PLAY_POLL):
            return


def _wait_unreaped(process: subprocess.Popen[str], timeout: float) -> bool:
    """Wait up to `timeout` for the worker to exit, without reaping it.

    Left unreaped, the exited worker keeps its PID, and with it its process
    group's id, so `_end_the_race` can kill the group with no chance of the
    id having been reused. Returns whether the worker exited.
    """
    try:
        pidfd = os.pidfd_open(process.pid)
    except (AttributeError, OSError):  # not Linux: reap it, a tiny window
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        return True
    try:
        ready, _, _ = select.select([pidfd], [], [], timeout)
    finally:
        os.close(pidfd)
    return bool(ready)


def _drain(pipe: IO[str], into: list[str]) -> None:
    """Read `pipe` to end-of-file, chunk by chunk, into `into`."""
    with pipe:
        while chunk := pipe.read(65536):
            into.append(chunk)


def _end_the_race(process: subprocess.Popen[str]) -> None:
    """Kill every process the race left behind, the worker included.

    First the worker's process group, which holds everything a controller
    started with `fork`, `subprocess` or `posix_spawn`. Then, under the
    deployed unit, the sweep, for anything that left the group with
    `setsid`. Races run one at a time, so at this point every process in
    the unit but the server is a leftover.
    """
    try:
        # The worker is not reaped yet (see `_wait_unreaped`), so the group's
        # id is still its own even when it has already exited.
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass  # no one left in the group
    process.wait()
    killed = sweep_unit()
    if killed:
        print(
            f"cocoracer-server: killed {killed} process(es) left behind by the race",
            file=sys.stderr,
            flush=True,
        )


def unit_procs_file(
    environ: dict[str, str] | None = None, proc_cgroup: str | None = None
) -> Path | None:
    """The unit's `cgroup.procs`, or None when not running as the unit.

    Only a process systemd started (`INVOCATION_ID` set) whose own cgroup is
    `UNIT_NAME` qualifies. `INVOCATION_ID` alone is not enough: a terminal
    started by a desktop session's systemd can pass it on to every shell,
    and a dev run from there must never sweep the desktop's processes. Both
    inputs can be given, for a test; they default to this process's own.
    """
    env = os.environ if environ is None else environ
    if not env.get("INVOCATION_ID"):
        return None
    if proc_cgroup is None:
        try:
            proc_cgroup = Path("/proc/self/cgroup").read_text()
        except OSError:
            return None
    for line in proc_cgroup.splitlines():
        # cgroup v2 only: "0::/system.slice/cocoracer-server.service".
        if not line.startswith("0::/"):
            continue
        path = line[len("0::") :]
        if path.rsplit("/", 1)[-1] == UNIT_NAME:
            return Path("/sys/fs/cgroup") / path.lstrip("/") / "cgroup.procs"
    return None


def pids_to_sweep(cgroup_procs: str, server_pid: int) -> list[int]:
    """Every PID listed in a `cgroup.procs`, but the server's own."""
    pids = [int(field) for field in cgroup_procs.split() if field.isdigit()]
    return [pid for pid in pids if pid != server_pid]


def sweep_unit(procs_file: Path | None = None) -> int:
    """SIGKILL every process in the unit but the server; return how many.

    A no-op returning 0 when not running as the unit (see
    `unit_procs_file`), so dev runs and tests never touch anything else.
    """
    if procs_file is None:
        procs_file = unit_procs_file()
    if procs_file is None:
        return 0
    try:
        listed = procs_file.read_text()
    except OSError:
        return 0
    killed = 0
    for pid in pids_to_sweep(listed, os.getpid()):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            continue
        killed += 1
    return killed
