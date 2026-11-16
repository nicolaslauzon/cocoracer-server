"""One race at a time, on a fixed cadence.

A qualification race every two minutes, plus official races the admin
triggers by hand. One lock admits one race at a time, so a qualification
race can never overlap an official one — the simplest arrangement that
cannot deadlock while sixteen people watch.

The policy (which cars, in what grid order, for how many laps) is separated
from the threads that drive it: `run_qualification` and `run_official` are
ordinary synchronous calls. The cadence thread decides when to call the
first; `start_official` hands the second to a one-shot thread, because a
race outlasts any request the admin is willing to wait on.
"""

import random
import threading
import time
from collections.abc import Callable, Sequence

from cocoracer.config import DEFAULT_PARAMS, load_config

from cocoracer_server.runner import (
    RACE_VIEW_PORT,
    Entry,
    RaceOutcome,
    run_race_in_subprocess,
)
from cocoracer_server.scoring import (
    RaceKind,
    RaceRecord,
    passes,
    qualification_scores,
)
from cocoracer_server.store import RacedCar, RaceReport, Store, Team

QUALIFICATION_INTERVAL = 120.0
OFFICIAL_LAPS = 3
DEFAULT_TRACK = "l"

# A qualification has no lap target: every car runs for one minute, and
# the field is classified by how far each car went.
QUALIFICATION_LAPS = None
QUALIFICATION_DURATION = 60.0
# The race countdown, which the time limit includes: read from the params
# the race worker races with, so the two cannot drift apart.
COUNTDOWN = load_config(DEFAULT_PARAMS).race.countdown

# Simulated seconds, the countdown included. The qualification's minute
# starts at the release. Official: only a backstop, for a race nobody
# finishes; a race with a winner ends OFFICIAL_AFTER_WINNER after it.
QUALIFICATION_TIME_LIMIT = COUNTDOWN + QUALIFICATION_DURATION
OFFICIAL_TIME_LIMIT = 300.0
# How often, in wall-clock seconds, the cadence thread checks whether a race
# is due; a race starts at most this late after its boundary.
CADENCE_TICK = 0.5
# An official race ends this many simulated seconds after its winner
# finishes (or at the time limit); the cars still out are done, and
# classified by distance behind the finishers.
OFFICIAL_AFTER_WINNER = 30.0

Runner = Callable[..., RaceOutcome]
# A race's field, its grid order, and the points that seeded it (official
# races only), snapshotted together once the race lock is held.
_LineUp = tuple[Sequence[Team], list[str], dict[str, int] | None]


class Scheduler:
    """Runs the event's races, and decides when."""

    def __init__(
        self,
        store: Store,
        runner: Runner = run_race_in_subprocess,
        track: str = DEFAULT_TRACK,
        interval: float = QUALIFICATION_INTERVAL,
        shuffle: Callable[[list[str]], None] = random.shuffle,
        live: bool = False,
        port: int = RACE_VIEW_PORT,
    ) -> None:
        self._store = store
        self._runner = runner
        self.track = track
        self.interval = interval
        self._shuffle = shuffle
        self.live = live
        self.port = port
        self._race_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._official_thread: threading.Thread | None = None
        # Guards the four fields below, which the cadence thread writes and
        # every admin request reads.
        self._state_lock = threading.Lock()
        self._paused = False
        self._next_race_at: float | None = None
        self._current: str | None = None
        # The Play press a live official race waits on, from its launch to
        # its end; None when no official race is launched.
        self._play: threading.Event | None = None

    @property
    def paused(self) -> bool:
        with self._state_lock:
            return self._paused

    @property
    def running_race(self) -> str | None:
        """The kind of race in progress, or None between races."""
        with self._state_lock:
            return self._current

    @property
    def awaiting_play(self) -> bool:
        """Whether a launched official race is waiting for the admin's Play.

        Only once it holds the race lock: launched while a qualification
        still runs, it is not on the grid yet, and Play is not offered.
        """
        with self._state_lock:
            return self._awaiting_play()

    def _awaiting_play(self) -> bool:
        return (
            self._play is not None
            and not self._play.is_set()
            and self._current == RaceKind.OFFICIAL.value
        )

    def play(self) -> bool:
        """Start the launched official race's countdown; False if none waits.

        The race waits on the grid, live, from its launch until this, for
        as long as it takes: nothing else starts it.
        """
        with self._state_lock:
            if self._play is None or not self._awaiting_play():
                return False
            self._play.set()
            return True

    @property
    def seconds_to_next_race(self) -> float | None:
        with self._state_lock:
            if self._next_race_at is None or self._paused:
                return None
            return max(0.0, self._next_race_at - time.monotonic())

    def pause(self) -> None:
        with self._state_lock:
            self._paused = True

    def resume(self) -> None:
        """Resume the cadence, with a full interval before the next race."""
        with self._state_lock:
            self._paused = False
            self._next_race_at = time.monotonic() + self.interval

    def run_qualification(self) -> RaceReport | None:
        """Race the whole field from a randomly shuffled grid, for a minute."""

        def line_up() -> _LineUp:
            field = self._store.teams()
            grid = [team.name for team in field]
            self._shuffle(grid)
            return field, grid, None

        return self._race(
            line_up,
            RaceKind.QUALIFICATION,
            QUALIFICATION_LAPS,
            QUALIFICATION_TIME_LIMIT,
        )

    def start_official(self) -> bool:
        """Start an official race in its own thread; False if one is running.

        An official race takes minutes, so the admin's request cannot wait on
        it, and a second click must not queue a second race behind the first.
        Served live, the race then waits on the grid for `play`, which the
        Live page offers the admin once the race holds the race lock.
        """
        with self._state_lock:
            running = self._official_thread is not None and (
                self._official_thread.is_alive()
            )
            if self._current is not None or running:
                return False
            play = threading.Event() if self.live else None
            self._play = play
            self._official_thread = threading.Thread(
                target=self._run_launched_official,
                args=(play,),
                name="official-race",
                daemon=True,
            )
            # Started while the lock is held: a constructed-but-unstarted
            # thread reports is_alive() False, so a second click landing in
            # that window would see no race running and start a second one.
            self._official_thread.start()
        return True

    def _run_launched_official(self, play: threading.Event | None) -> None:
        try:
            self.run_official(play)
        finally:
            with self._state_lock:
                if self._play is play:
                    self._play = None

    def run_official(self, play: threading.Event | None = None) -> RaceReport | None:
        """Race the field from a grid seeded by qualification score.

        Pushes stay open: like any race, it races the sources snapshotted
        when it takes the race lock, so a push that lands during it belongs
        to the next race. With `play`, the race waits on the grid until it
        is set (see `run_race_in_subprocess`).
        """

        def line_up() -> _LineUp:
            field = self._store.teams()
            points = self._official_points(field)
            grid = sorted(points, key=lambda name: (-points[name], name))
            return field, grid, points

        return self._race(
            line_up,
            RaceKind.OFFICIAL,
            OFFICIAL_LAPS,
            OFFICIAL_TIME_LIMIT,
            after_winner=OFFICIAL_AFTER_WINNER,
            play=play,
        )

    def _official_points(self, field: Sequence[Team]) -> dict[str, int]:
        """Each team's qualification score now, which seeds the official grid
        and is recorded with the race so its page can show why. A team that
        took a deleted team's name starts on its own points, not the old
        team's (`Store.scoring_records`)."""
        scores = qualification_scores(self._store.scoring_records())
        return {team.name: scores.get(team.name, 0) for team in field}

    def _race(
        self,
        line_up: Callable[[], _LineUp],
        kind: RaceKind,
        laps: int | None,
        time_limit: float,
        after_winner: float | None = None,
        play: threading.Event | None = None,
    ) -> RaceReport | None:
        """Run one race and record it, all under the race lock.

        `line_up` snapshots the field, its grid and (for an official race)
        the points that seeded it. It is called once the lock is held, and
        the race is recorded before the lock is let go, so a race launched
        while another runs sees that one's result: an official race
        triggered mid-qualification is seeded with that qualification in.
        """
        with self._race_lock:
            field, grid, qualification_points = line_up()
            # One snapshot serves the grid order, the sources, their
            # version numbers and whose they are: reading the field twice
            # would let a push land between them and race, or be reported as
            # having raced, or a name taken again be reported as its new
            # team's.
            sources = {team.name: team.source for team in field}
            cars = {
                team.name: RacedCar(team.registered_at, team.pushes) for team in field
            }
            entries = [Entry(name, sources[name]) for name in grid if name in sources]
            if not entries:
                return None
            with self._state_lock:
                self._current = kind.value
            try:
                outcome = self._runner(
                    entries,
                    track=self.track,
                    laps=laps,
                    time_limit=time_limit,
                    after_winner=after_winner,
                    live=self.live,
                    port=self.port,
                    play=play,
                )
            finally:
                with self._state_lock:
                    self._current = None
            replay = None if outcome.void else outcome.replay
            return self._store.record_race(
                self._report(
                    entries,
                    kind,
                    laps,
                    outcome,
                    qualification_points,
                    cars,
                ),
                replay,
                outcome.logs,
            )

    def _report(
        self,
        entries: Sequence[Entry],
        kind: RaceKind,
        laps: int | None,
        outcome: RaceOutcome,
        qualification_points: dict[str, int] | None,
        cars: dict[str, RacedCar],
    ) -> RaceReport:
        grid = tuple(entry.name for entry in entries)
        classified = () if outcome.void else outcome.classified
        counted = None
        if outcome.lap_length is not None and set(outcome.distances) == set(grid):
            counted = passes(grid, outcome.distances, outcome.lap_length)
        return RaceReport(
            record=RaceRecord(
                kind=kind, grid=grid, classified=classified, passes=counted
            ),
            track=self.track,
            laps=laps,
            finished_at=time.time(),
            statuses=dict(outcome.statuses),
            faults=dict(outcome.faults),
            void=outcome.void,
            void_reason=outcome.void_reason,
            qualification_points=None
            if qualification_points is None
            else {name: qualification_points[name] for name in grid},
            cars={name: cars[name] for name in grid},
        )

    def start(self) -> None:
        """Start the cadence in a background thread."""
        if self._thread is not None:
            return
        self._stop.clear()
        with self._state_lock:
            self._next_race_at = time.monotonic() + self.interval
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5.0)

    def _loop(self) -> None:
        while not self._stop.wait(CADENCE_TICK):
            with self._state_lock:
                scheduled = None if self._paused else self._next_race_at
            if scheduled is None or time.monotonic() < scheduled:
                continue
            try:
                self.run_qualification()
            except Exception:  # one bad race must not end the cadence
                pass
            # Advance *after* the race, not before: a race that overran its
            # slot has to skip the boundaries it ate, or the loop fires the
            # next one immediately and the cadence collapses into back-to-back
            # races. A pause or a resume during the race has already set the
            # next boundary, and that decision wins over this arithmetic.
            with self._state_lock:
                if self._next_race_at == scheduled:
                    self._next_race_at = next_boundary(
                        scheduled, self.interval, time.monotonic()
                    )


def next_boundary(previous: float, interval: float, now: float) -> float:
    """The first cadence boundary after `previous` that is still ahead.

    A race that overruns its slot eats its own slack: the cadence skips the
    boundaries the race ate and keeps its original phase, rather than
    drifting later by however long the overrun was.
    """
    nxt = previous + interval
    if nxt <= now:
        missed = int((now - previous) // interval) + 1
        nxt = previous + missed * interval
    return nxt
