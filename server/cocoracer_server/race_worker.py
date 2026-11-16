"""Child process that runs exactly one race and writes the result as JSON.

Run as `python -m cocoracer_server.race_worker`, fed one request on stdin.
It lives in its own process so the parent can kill it (ADR 0002), and every
car's controller lives in a process of its own, a child of this one
(`car_process` and `car_field`, ADR 0007): nothing a controller does can
stop the race for the rest of the field, only DNF its own car.

The request comes in two parts, so that no car is ever forked with another
team's source in its memory: first a line of JSON with the race and the
names on the grid, on which the worker forks one process per car; then one
line per car, its source as a JSON string, in grid order. Only then is each
source sent to its own car. With `"wait_for_play"`, a last line `PLAY`
starts the race.

The result goes out on stdout, but only through a private duplicate of that
descriptor taken before any car starts (see `main`).

With `"live"` set it paces the race to the wall clock and serves cocoracer's
live view on `"port"` of every interface, so every team on the venue LAN can
watch the race from the Live tab, whose player reads the view's WebSocket.
The view is read-only: it gets no start queue, so nothing a viewer sends
reaches the race. Without Play, a live race holds the field on the grid for
`"preroll"` seconds of wall clock, counted from the view accepting
connections, so a viewer who joins as the race comes up sees its whole
countdown. `"hold"` is how long the final frame stays up before the
server stops, so the result is readable before the next race replaces it.
Headless it simulates as fast as it can, which is what most of the tests
want.

Either way the race is also recorded for replay, sampled at `REPLAY_HZ` of
simulated time in the format the replay player reads: the live view's
static message, the names in grid order, and per frame one
`[x, y, yaw, speed, laps, status, crashes, distance, finish_time]` row
per car, in that order (`Recorder`).

The cars' logs come back beside the replay, not inside it, because only
each car's own team may read its log: for every car that printed anything,
its lines as `[t, text]`, `t` the race time of the call that printed it,
and whether it went over `LOG_BUDGET_CHARS` and lost the rest.

Request:  {"track", "laps", "time_limit", "after_winner", "workdir", "live",
           "port", "hold", "preroll", "wait_for_play", "entries": [{"name"}]}
          then one JSON string per entry: its source.
          ("laps" and "after_winner" may be null.)
Response: {"classified": [...], "statuses": {...}, "faults": {...},
           "distances": {name: [at the release, at the end]}, "lap_length",
           "replay": {"static", "names", "hz", "frames": [...]},
           "logs": {name: {"lines": [[t, text], ...], "truncated"}}}
"""

import os

# One BLAS thread, set before numpy is first imported: the cars, forked from
# this process, already run in parallel, so more would only oversubscribe
# the cores; it brings this process's address space after the imports from
# about 1.5 GB to 250 MB, which every car's RLIMIT_AS starts from; and it
# leaves no BLAS thread behind to fork.
for _blas in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_blas] = "1"

import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from collections.abc import Callable, Sequence  # noqa: E402
from functools import partial  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

from cocoracer.classification import RaceResult  # noqa: E402
from cocoracer.config import DEFAULT_PARAMS, load_config  # noqa: E402
from cocoracer.engine import RaceEngine  # noqa: E402
from cocoracer.track import build_track  # noqa: E402
from cocoracer.web.protocol import build_static_message  # noqa: E402

from cocoracer_server.car_field import Car, CarField, collect, start_cars  # noqa: E402
from cocoracer_server.car_process import volunteer_for_the_oom_killer  # noqa: E402
from cocoracer_server.sandbox import make_undumpable  # noqa: E402

# Every interface, so the teams on the venue LAN reach the live view too.
LIVE_VIEW_HOST = "0.0.0.0"

# Seconds the final frame stays up in the live view before the server stops.
DEFAULT_HOLD = 2.0

# Seconds a live race without Play holds the field on the grid, from its view
# accepting connections, when the request names none.
DEFAULT_PREROLL = 2.0

# Replay frames per second of simulated time. The player interpolates
# between them, so this buys size, not smoothness.
REPLAY_HZ = 10


class Recorder:
    """Samples one race into a replay, a frame every `1/hz` s of sim time.

    The first frame is taken at construction, before any tick, so the
    replay opens on the field as it was released; `replay` adds the final
    frame if the last sample fell short of it. `observe` must be called
    once after every tick.

    A car's row carries the engine's own `distance` (metres from the
    start/finish line) and `finish_time` (null until it finishes), so the
    player ranks its tower by the classification's rule rather than
    working the order out from the poses.
    """

    # A car row's columns, in order; `static/replay.js` indexes rows by them.
    COLUMNS: tuple[str, ...] = (
        "x",
        "y",
        "yaw",
        "speed",
        "laps",
        "status",
        "crashes",
        "distance",
        "finish_time",
    )

    def __init__(self, engine: RaceEngine, hz: int = REPLAY_HZ) -> None:
        self._engine = engine
        self._hz = hz
        self._every = max(1, round(1 / (hz * engine.config.sim.tick_dt)))
        self._ticks = 0
        self._sampled_at = -1
        self._static = json.loads(build_static_message(engine.track, engine.config))
        self._names = [v.name for v in engine.vehicles]
        self._frames: list[dict[str, Any]] = []
        self._sample()

    def _sample(self) -> None:
        snapshot = self._engine.snapshot()
        self._frames.append(
            {
                "t": round(float(snapshot.time), 3),
                "phase": self._engine.phase,
                "countdown": round(float(self._engine.countdown), 3),
                "cars": [
                    [
                        round(float(v.x), 2),
                        round(float(v.y), 2),
                        round(float(v.yaw), 2),
                        round(float(v.speed), 2),
                        int(v.race.laps_completed),
                        v.race.status.value,
                        int(v.race.crashes),
                        round(float(v.distance), 2),
                        None
                        if v.race.finish_time is None
                        else round(float(v.race.finish_time), 3),
                    ]
                    for v in snapshot.vehicles
                ],
            }
        )
        self._sampled_at = self._ticks

    def observe(self) -> None:
        """Count one tick, and sample it if it lands on the replay's rate."""
        self._ticks += 1
        if self._ticks % self._every == 0:
            self._sample()

    def replay(self) -> dict[str, Any]:
        if self._sampled_at != self._ticks:
            self._sample()
        return {
            "static": self._static,
            "names": self._names,
            "hz": self._hz,
            "frames": self._frames,
        }


def _slug(name: str, index: int) -> str:
    """A filename that names the team, so a traceback is readable."""
    safe = "".join(c if c.isalnum() else "_" for c in name)[:40]
    return f"{index:02d}_{safe or 'team'}"


def run(
    request: dict[str, Any],
    sources: Sequence[str],
    play: Callable[[], None] | None = None,
    cars: list[Car] | None = None,
) -> dict[str, Any]:
    """Run the race `request` describes, `sources[i]` driving its i-th
    entry, and return its result.

    `cars` are the entries' processes, forked beforehand by `main` while no
    source was in this process yet; without them they are forked here. They
    are all killed before this returns.

    With `play`, the field waits on the grid (live, its cars and names
    shown) until `play` returns, and the countdown runs from there.
    """
    config = load_config(DEFAULT_PARAMS)
    laps = request["laps"]
    config.race.laps = None if laps is None else int(laps)
    config.race.time_limit = float(request["time_limit"])
    after_winner = request.get("after_winner")
    config.race.after_winner = None if after_winner is None else float(after_winner)
    track = build_track(config.tracks[request["track"]])
    names = [entry["name"] for entry in request["entries"]]
    if cars is None:
        cars = start_cars(names)
    # The parent owns the directory and removes it even when this process is
    # killed for hanging, which is the case the whole runner exists for.
    directory = Path(request["workdir"])
    try:
        engine = RaceEngine(
            track,
            config,
            names=names,
            mode="race",
            auto_start=False,
            field=field(cars, sources, directory, config.baselines),
        )
        for car in cars:
            car.clock = lambda: engine.time
        result, replay, released = _race(engine, request, play)
    finally:
        for car in cars:
            car.close()
    return {
        "classified": [r.name for r in result.results],
        "statuses": {r.name: r.race.status.value for r in result.results},
        "faults": {
            r.name: r.race.fault_reason for r in result.results if r.race.fault_reason
        },
        # What the server counts passes from (`scoring.passes`).
        "distances": {v.name: [released[v.name], v.distance] for v in engine.vehicles},
        "lap_length": track.track_length,
        "replay": replay,
        "logs": {car.name: car.log.to_json() for car in cars if car.log.lines},
    }


def field(
    cars: Sequence[Car],
    sources: Sequence[str],
    directory: Path,
    baselines: dict[str, Any],
) -> CarField:
    """Load every car's source, all cars at once, and field them.

    Each import has `LOAD_TIME_LIMIT`, counted from its own send, so a slow
    car costs the field its own wait and no more. A car that would not load
    keeps its grid slot, and its process is gone: the team is classified
    last rather than dropped from the field, because the other cars'
    positions gained are measured against the grid they actually started
    on. The engine, built next, resets the cars that loaded; one that would
    not faults at its first step, with the reason it would not
    (`CarField`).
    """
    for i, (car, source) in enumerate(zip(cars, sources, strict=True)):
        path = directory / f"{_slug(car.name, i)}.py"
        car.send_load(source, str(path), baselines)
    collect(cars)
    for car in cars:
        if car.fault is not None:
            car.close()
    return CarField(cars)


def _race(
    engine: RaceEngine, request: dict[str, Any], play: Callable[[], None] | None
) -> tuple[RaceResult, dict[str, Any], dict[str, float]]:
    """Run `engine` to the end, live or headless, and record its replay,
    and each car's distance as the field was released.

    With `play`, the field waits on the grid, shown live, until `play`
    returns. Without it nobody is at the keyboard (a qualification): live,
    the field still waits, for the request's `"preroll"`, counted from the
    view accepting connections rather than from this process's start, which
    the cars and uvicorn take a second or two of; headless, it starts at
    once. The view gets no start queue, so nothing a viewer sends starts
    it. The replay opens on the countdown however long the grid waited.
    """
    view = None
    if request.get("live"):
        # Imported here: fastapi and uvicorn cost a third of a second, and
        # most races are headless.
        from cocoracer.web.server import WebServer

        view = WebServer(engine, port=int(request["port"]), host=LIVE_VIEW_HOST)
        # Returns once the socket accepts connections.
        view.start()
        if play is None:
            play = partial(time.sleep, float(request.get("preroll", DEFAULT_PREROLL)))

    try:
        if play is not None:
            play()
        engine.start()
        # The countdown re-anchors the field where it stands, so this is
        # its distance at the release too.
        released = {v.name: v.distance for v in engine.vehicles}
        recorder = Recorder(engine)
        period = engine.config.sim.tick_dt
        next_tick = time.monotonic()
        while not engine.finished:
            engine.tick()
            recorder.observe()
            if view is not None:
                next_tick += period
                time.sleep(max(0.0, next_tick - time.monotonic()))
        if view is not None:
            # Hold the final frame so the result is readable before the
            # next race replaces it.
            time.sleep(float(request.get("hold", DEFAULT_HOLD)))
    finally:
        if view is not None:
            view.stop()
    return engine.results, recorder.replay(), released


class _Input:
    """The worker's stdin, read with `os.read` rather than `sys.stdin`, so
    nothing is left in a buffer the play channel cannot see."""

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self.pending = b""

    def first_line(self) -> bytes:
        """The first line, read a byte at a time so that not one byte past
        it, the sources that follow, is read before the cars are forked."""
        data = bytearray()
        while not data.endswith(b"\n"):
            byte = os.read(self._fd, 1)
            if not byte:
                break
            data += byte
        return bytes(data)

    def line(self) -> bytes:
        data = bytearray(self.pending)
        while b"\n" not in data:
            chunk = os.read(self._fd, 65536)
            if not chunk:
                break
            data += chunk
        line, _, rest = bytes(data).partition(b"\n")
        self.pending = rest
        return line


# The worker's exit status when its stdin closes before Play.
NO_PLAY_EXIT = 3


def _play_channel(fd: int, pending: bytes) -> Callable[[], None]:
    """What `run` waits on for Play: the next line on `fd`, the private copy
    of stdin the runner writes it to.

    End-of-file first means the runner is gone (a server restarted outside
    systemd, which would not have killed this process): the worker exits
    at once with `NO_PLAY_EXIT` rather than race alone on the race port.
    """

    def play() -> None:
        data = bytearray(pending)
        while b"\n" not in data:
            chunk = os.read(fd, 4096)
            if not chunk:
                os.write(2, b"race_worker: stdin closed before Play\n")
                sys.exit(NO_PLAY_EXIT)
            data += chunk

    return play


# Written to `/proc/self/oom_score_adj`: well above the server's 0, so the
# kernel picks the worker before the server, and below every car's 1000
# (`car_process.OOM_SCORE_ADJ`), so it picks a car first. The worker and the
# server share the unit's cgroup, and past its `MemoryMax` the kernel kills
# whichever process scores highest: a car first, which DNFs while the race
# goes on; failing that the worker, which voids the race; never the server.
OOM_SCORE_ADJ = 500


def main() -> int:
    volunteer_for_the_oom_killer(OOM_SCORE_ADJ)
    stdin = _Input(0)
    request = json.loads(stdin.first_line())
    names = [entry["name"] for entry in request["entries"]]
    # Every car's process is forked now, before any source is read: each
    # starts with a copy of this process, which must not hold the others'.
    cars = start_cars(names)
    # And none of them may open this process's /proc files: the other cars'
    # pipes, the result, the sources in memory. After the OOM score, which a
    # non-dumpable process can no longer write.
    make_undumpable()
    sources = [json.loads(stdin.line()) for _ in names]
    # The runner writes Play on stdin: keep a private copy of fd 0 for the
    # play channel and give fd 0 /dev/null.
    play = None
    if request.get("wait_for_play"):
        play = _play_channel(os.dup(0), stdin.pending)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    # Keep a private copy of fd 1 for the result, and point fd 1 itself at
    # stderr, so that nothing else this process prints (a library's warning,
    # say) can reach the channel the parent parses the result from.
    result_fd = os.dup(1)
    os.dup2(2, 1)
    try:
        result = run(request, sources, play, cars)
    finally:
        sys.stdout.flush()
    with os.fdopen(result_fd, "w") as out:
        json.dump(result, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
