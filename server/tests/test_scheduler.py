"""Race cadence, grid seeding, pushes during a race, and surviving a void race."""

import threading
import time
from collections.abc import Callable

import pytest

from cocoracer_server import scheduler as scheduler_module
from cocoracer_server.runner import RaceOutcome
from cocoracer_server.scheduler import QUALIFICATION_INTERVAL, Scheduler, next_boundary
from cocoracer_server.scoring import RaceKind
from cocoracer_server.store import RaceReport, Store
from tests.conftest import CAR, RecordingRunner, enter, key

NEXT_CAR = CAR + "\n# the next version\n"


def _versions(report: RaceReport) -> dict[str, int | None]:
    """The version number each car of `report` raced, by name."""
    return {name: car.version for name, car in report.cars.items()}


def _wait_for(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 5.0
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert condition()


@pytest.fixture
def teams() -> tuple[str, ...]:
    return ("alpha", "bravo", "charlie")


@pytest.fixture
def shuffle() -> Callable[[list[str]], None]:
    # A shuffle that reverses is deterministic but still not the input order,
    # so "the grid was shuffled" is observable.
    return lambda names: names.reverse()


class TestQualification:
    def test_it_races_the_whole_field_for_one_minute_with_no_lap_target(
        self, scheduler: Scheduler, runner: RecordingRunner
    ) -> None:
        report = scheduler.run_qualification()
        assert report is not None
        assert report.record.kind is RaceKind.QUALIFICATION
        assert report.laps is None
        assert runner.calls[0]["laps"] is None
        # A minute of racing after the three-second countdown.
        assert runner.calls[0]["time_limit"] == 63.0
        assert runner.calls[0]["after_winner"] is None
        assert set(report.record.grid) == {"alpha", "bravo", "charlie"}

    def test_the_grid_is_shuffled_not_taken_in_registration_order(
        self, scheduler: Scheduler, runner: RecordingRunner
    ) -> None:
        scheduler.run_qualification()
        assert runner.calls[0]["grid"] == ["charlie", "bravo", "alpha"]

    def test_the_result_is_recorded_and_scores(
        self, scheduler: Scheduler, store: Store
    ) -> None:
        scheduler.run_qualification()
        assert len(store.races()) == 1
        scores = {s.team: s.qualification_score for s in store.standings()}
        # Grid charlie, bravo, alpha; finish reversed, so alpha gains 2.
        assert scores["alpha"] == 2
        assert scores["charlie"] == 0

    def test_an_empty_field_races_nothing(self, runner: RecordingRunner) -> None:
        scheduler = Scheduler(Store(), runner=runner)
        assert scheduler.run_qualification() is None
        assert runner.calls == []

    def test_a_team_added_between_races_joins_the_next_one(
        self, scheduler: Scheduler, store: Store, runner: RecordingRunner
    ) -> None:
        scheduler.run_qualification()
        enter(store, "late")
        scheduler.run_qualification()
        assert "late" not in runner.calls[0]["grid"]  # type: ignore[operator]
        assert "late" in runner.calls[1]["grid"]  # type: ignore[operator]


class TestOfficial:
    def test_a_push_during_it_is_accepted_and_races_next_time(
        self, scheduler: Scheduler, store: Store, runner: RecordingRunner
    ) -> None:
        runner.gate = threading.Event()
        try:
            assert scheduler.start_official() is True
            assert runner.started.wait(5.0)
            store.push(key(store, "alpha"), NEXT_CAR)
        finally:
            runner.gate.set()
        _wait_for(lambda: len(store.races()) == 1)
        assert runner.calls[0]["sources"]["alpha"] == CAR  # type: ignore[index]
        scheduler.run_official()
        assert runner.calls[1]["sources"]["alpha"] == NEXT_CAR  # type: ignore[index]

    def test_a_push_during_it_does_not_change_the_versions_it_reports(
        self, scheduler: Scheduler, store: Store, runner: RecordingRunner
    ) -> None:
        runner.gate = threading.Event()
        try:
            assert scheduler.start_official() is True
            assert runner.started.wait(5.0)
            store.push(key(store, "alpha"), NEXT_CAR)
        finally:
            runner.gate.set()
        _wait_for(lambda: len(store.races()) == 1)
        assert _versions(store.races()[0]) == {"alpha": 1, "bravo": 1, "charlie": 1}
        report = scheduler.run_official()
        assert report is not None
        assert _versions(report)["alpha"] == 2

    def test_it_races_three_laps_ending_30s_after_the_winner(
        self, scheduler: Scheduler, runner: RecordingRunner
    ) -> None:
        scheduler.run_official()
        assert runner.calls[0]["laps"] == 3
        assert runner.calls[0]["after_winner"] == 30.0

    def test_the_grid_is_seeded_by_qualification_score(
        self, scheduler: Scheduler, runner: RecordingRunner
    ) -> None:
        scheduler.run_qualification()  # alpha +2, bravo +0, charlie +0
        runner.calls.clear()
        scheduler.run_official()
        assert runner.calls[0]["grid"] == ["alpha", "bravo", "charlie"]

    def test_it_records_the_points_its_grid_was_seeded_from(
        self, scheduler: Scheduler, store: Store
    ) -> None:
        scheduler.run_qualification()  # alpha +2, bravo +0, charlie +0
        official = scheduler.run_official()
        assert official is not None
        assert official.qualification_points == {
            "alpha": 2,
            "bravo": 0,
            "charlie": 0,
        }
        # Qualifications go on between official races; the official race
        # keeps the points it was seeded from.
        scheduler.run_qualification()
        assert store.races()[1].qualification_points == official.qualification_points

    def test_a_qualification_records_the_version_each_team_raced(
        self, scheduler: Scheduler, store: Store
    ) -> None:
        store.push(key(store, "bravo"), NEXT_CAR)
        report = scheduler.run_qualification()
        assert report is not None
        assert _versions(report) == {"alpha": 1, "bravo": 2, "charlie": 1}

    def test_a_team_that_took_a_deleted_name_is_seeded_on_its_own_points(
        self, scheduler: Scheduler, store: Store
    ) -> None:
        scheduler.run_qualification()  # alpha +2, bravo +0, charlie +0
        store.delete("alpha")
        enter(store, "alpha", password="other")
        official = scheduler.run_official()
        assert official is not None
        assert official.qualification_points == {
            "alpha": 0,
            "bravo": 0,
            "charlie": 0,
        }

    def test_a_race_records_whose_cars_it_had(
        self, scheduler: Scheduler, store: Store
    ) -> None:
        report = scheduler.run_qualification()
        assert report is not None
        registered = {name: car.registered_at for name, car in report.cars.items()}
        assert registered == {t.name: t.registered_at for t in store.teams()}

    def test_a_qualification_records_no_points(self, scheduler: Scheduler) -> None:
        report = scheduler.run_qualification()
        assert report is not None and report.qualification_points is None

    def test_official_races_do_not_feed_the_qualification_score(
        self, scheduler: Scheduler, store: Store
    ) -> None:
        scheduler.run_official()
        assert all(s.qualification_score == 0 for s in store.standings())

    def test_three_official_races_sum_the_finishing_positions(
        self, scheduler: Scheduler, store: Store
    ) -> None:
        for _ in range(3):
            scheduler.run_official()
        standings = {s.team: s for s in store.standings()}
        # Grid is by qualification score then name; the field always finishes
        # reversed, so the last name on the grid wins all three.
        assert standings["charlie"].official_score == 3
        assert standings["alpha"].official_score == 9
        assert [s.team for s in store.standings()][0] == "charlie"


class TestOfficialInTheBackground:
    def test_it_returns_before_the_race_is_over(
        self, scheduler: Scheduler, runner: RecordingRunner, store: Store
    ) -> None:
        runner.gate = threading.Event()
        try:
            assert scheduler.start_official() is True
            assert runner.started.wait(5.0)
            assert store.races() == []
            assert scheduler.running_race == "official"
        finally:
            runner.gate.set()

    def test_a_second_trigger_is_refused_while_a_race_runs(
        self, scheduler: Scheduler, runner: RecordingRunner
    ) -> None:
        """Two clicks must not queue two official races."""
        runner.gate = threading.Event()
        try:
            assert scheduler.start_official() is True
            assert runner.started.wait(5.0)
            assert scheduler.start_official() is False
        finally:
            runner.gate.set()
        assert len(runner.calls) == 1

    def test_the_race_is_recorded_once_it_finishes(
        self, scheduler: Scheduler, store: Store, runner: RecordingRunner
    ) -> None:
        assert scheduler.start_official() is True
        deadline = time.monotonic() + 5.0
        while not store.races() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert len(store.races()) == 1


class TestOfficialWaitingOnAQualification:
    def test_it_is_seeded_with_the_qualification_it_waited_for(
        self, scheduler: Scheduler, runner: RecordingRunner, store: Store
    ) -> None:
        # Grid charlie, bravo, alpha (the shuffle reverses): bravo gains one,
        # so the official grid is bravo, alpha, charlie -- not name order.
        runner.outcome = RaceOutcome(
            classified=("bravo", "charlie", "alpha"),
            statuses={name: "finished" for name in ("alpha", "bravo", "charlie")},
        )
        runner.gate = threading.Event()
        qualification = threading.Thread(target=scheduler.run_qualification)
        qualification.start()
        official = threading.Thread(target=scheduler.run_official)
        try:
            assert runner.started.wait(5.0)
            official.start()
            time.sleep(0.05)  # the official race is waiting on the race lock
        finally:
            runner.gate.set()
        qualification.join(5.0)
        official.join(5.0)
        assert runner.calls[1]["grid"] == ["bravo", "alpha", "charlie"]
        assert store.races()[1].qualification_points == {
            "alpha": 0,
            "bravo": 1,
            "charlie": 0,
        }


class TestPlay:
    """A live official race waits on the grid for the admin's Play."""

    @pytest.fixture
    def live(self, store: Store, runner: RecordingRunner) -> Scheduler:
        return Scheduler(store, runner=runner, live=True)

    def test_a_launched_race_waits_for_play_and_play_releases_it(
        self, live: Scheduler, runner: RecordingRunner
    ) -> None:
        runner.gate = threading.Event()
        try:
            assert live.start_official() is True
            assert runner.started.wait(5.0)
            assert live.awaiting_play
            play = runner.calls[0]["play"]
            assert isinstance(play, threading.Event) and not play.is_set()
            assert live.play() is True
            assert play.is_set()
            assert not live.awaiting_play
            assert live.play() is False
        finally:
            runner.gate.set()

    def test_nothing_waits_once_the_race_is_over(
        self, live: Scheduler, store: Store, runner: RecordingRunner
    ) -> None:
        runner.gate = threading.Event()
        assert live.start_official() is True
        assert runner.started.wait(5.0)
        assert live.play() is True
        runner.gate.set()
        deadline = time.monotonic() + 5.0
        while live.running_race is not None or not store.races():
            assert time.monotonic() < deadline
            time.sleep(0.005)
        assert not live.awaiting_play
        assert live.play() is False

    def test_play_is_not_offered_until_the_race_holds_the_lock(
        self, live: Scheduler, runner: RecordingRunner
    ) -> None:
        """Launched while another race still holds the lock (a qualification
        that started in the same instant), the official race is not on the
        grid yet: Play is neither offered nor accepted."""
        runner.gate = threading.Event()
        try:
            with live._race_lock:
                assert live.start_official() is True
                assert not live.awaiting_play
                assert live.play() is False
            assert runner.started.wait(5.0)
            assert live.awaiting_play
            assert live.play() is True
        finally:
            runner.gate.set()

    def test_play_with_no_race_launched_is_refused(self, live: Scheduler) -> None:
        assert live.play() is False
        assert not live.awaiting_play

    def test_qualifications_never_wait(
        self, live: Scheduler, runner: RecordingRunner
    ) -> None:
        live.run_qualification()
        assert runner.calls[0]["play"] is None

    def test_a_headless_server_has_no_live_view_to_press_play_on(
        self, scheduler: Scheduler, runner: RecordingRunner
    ) -> None:
        runner.gate = threading.Event()
        try:
            assert scheduler.start_official() is True
            assert not scheduler.awaiting_play
            assert runner.started.wait(5.0)
            assert runner.calls[0]["play"] is None
        finally:
            runner.gate.set()


class TestVoidRaces:
    def test_a_void_race_is_recorded_but_does_not_score(
        self, scheduler: Scheduler, store: Store, runner: RecordingRunner
    ) -> None:
        runner.outcome = RaceOutcome.voided("a controller is stuck in a loop")
        report = scheduler.run_qualification()
        assert report is not None and report.void
        assert store.scoring_records() == []
        assert all(s.qualification_score == 0 for s in store.standings())

    def test_a_void_race_keeps_its_reason_for_the_admin(
        self, scheduler: Scheduler, runner: RecordingRunner
    ) -> None:
        runner.outcome = RaceOutcome.voided("a controller is stuck in a loop")
        report = scheduler.run_qualification()
        assert report is not None
        assert report.void_reason is not None
        assert "stuck in a loop" in report.void_reason

    def test_the_next_race_runs_normally_after_a_void_one(
        self, scheduler: Scheduler, store: Store, runner: RecordingRunner
    ) -> None:
        runner.outcome = RaceOutcome.voided("stuck")
        scheduler.run_qualification()
        runner.outcome = None
        scheduler.run_qualification()
        assert len(store.scoring_records()) == 1

    def test_a_void_race_has_no_replay(
        self, scheduler: Scheduler, store: Store, runner: RecordingRunner
    ) -> None:
        runner.outcome = RaceOutcome.voided("stuck")
        report = scheduler.run_qualification()
        assert report is not None and store.replay(report.index) is None


class TestReplays:
    def test_the_replay_is_recorded_with_the_race(
        self, scheduler: Scheduler, store: Store, runner: RecordingRunner
    ) -> None:
        replay = b'{"static":{},"names":["alpha"],"hz":10,"frames":[]}'
        runner.outcome = RaceOutcome(
            classified=("alpha", "bravo", "charlie"),
            statuses={"alpha": "finished"},
            replay=replay,
        )
        report = scheduler.run_qualification()
        assert report is not None and store.replay(report.index) == replay


class TestPasses:
    def test_a_race_records_the_passes_its_distances_make(
        self, scheduler: Scheduler, runner: RecordingRunner
    ) -> None:
        # Grid charlie, bravo, alpha (the shuffle reverses); charlie laps
        # alpha, bravo passes nobody.
        runner.outcome = RaceOutcome(
            classified=("charlie", "bravo", "alpha"),
            distances={
                "charlie": (0.0, 250.0),
                "bravo": (-4.0, 160.0),
                "alpha": (-8.0, 120.0),
            },
            lap_length=100.0,
        )
        report = scheduler.run_qualification()
        assert report is not None
        assert report.record.passes == (1, 0, -1)

    def test_a_race_without_distances_records_none(
        self, scheduler: Scheduler, runner: RecordingRunner
    ) -> None:
        runner.outcome = RaceOutcome(classified=("charlie", "bravo", "alpha"))
        report = scheduler.run_qualification()
        assert report is not None and report.record.passes is None


# Several cadence boundaries at `interval=0.05`: long enough that a loop
# which should not race would have, short enough to keep each test quick.
_SEVERAL_BOUNDARIES = 0.2


@pytest.fixture
def fast_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check for a due race every 10 ms, so a 50 ms cadence is kept."""
    monkeypatch.setattr(scheduler_module, "CADENCE_TICK", 0.01)


class TestCadence:
    def test_pausing_and_resuming_is_observable(self, scheduler: Scheduler) -> None:
        assert not scheduler.paused
        scheduler.pause()
        assert scheduler.paused
        assert scheduler.seconds_to_next_race is None
        scheduler.resume()
        assert not scheduler.paused
        assert scheduler.seconds_to_next_race == pytest.approx(
            QUALIFICATION_INTERVAL, abs=1.0
        )

    def test_the_thread_starts_and_stops_cleanly(self, scheduler: Scheduler) -> None:
        scheduler.start()
        assert scheduler.seconds_to_next_race is not None
        scheduler.stop()

    @pytest.mark.usefixtures("fast_tick")
    def test_the_loop_races_on_the_boundary_and_keeps_one_ahead(
        self, store: Store, runner: RecordingRunner
    ) -> None:
        """A smoke test over the real thread; the arithmetic it relies on is
        next_boundary's, tested below without a clock to wait on."""
        scheduler = Scheduler(store, runner=runner, interval=0.05)
        scheduler.start()
        try:
            assert runner.started.wait(5.0), "the cadence never fired a race"
        finally:
            scheduler.stop()
        assert len(runner.calls) >= 1
        assert scheduler.seconds_to_next_race is not None

    @pytest.mark.usefixtures("fast_tick")
    def test_a_paused_cadence_races_nothing(
        self, store: Store, runner: RecordingRunner
    ) -> None:
        scheduler = Scheduler(store, runner=runner, interval=0.05)
        scheduler.pause()
        scheduler.start()
        try:
            time.sleep(_SEVERAL_BOUNDARIES)
        finally:
            scheduler.stop()
        assert runner.calls == []

    @pytest.mark.usefixtures("fast_tick")
    def test_a_runner_that_raises_does_not_end_the_cadence(
        self, store: Store, runner: RecordingRunner
    ) -> None:
        def broken(*args: object, **kwargs: object) -> RaceOutcome:
            runner(*args, **kwargs)  # type: ignore[arg-type]
            raise RuntimeError("the race process blew up")

        scheduler = Scheduler(store, runner=broken, interval=0.05)
        scheduler.start()
        try:
            _wait_for(lambda: len(runner.calls) >= 2)
        finally:
            scheduler.stop()
        assert scheduler.running_race is None

    @pytest.mark.usefixtures("fast_tick")
    def test_no_race_starts_after_stop(
        self, store: Store, runner: RecordingRunner
    ) -> None:
        scheduler = Scheduler(store, runner=runner, interval=0.05)
        scheduler.start()
        assert runner.started.wait(5.0), "the cadence never fired a race"
        scheduler.stop()
        races = len(runner.calls)
        time.sleep(_SEVERAL_BOUNDARIES)
        assert len(runner.calls) == races

    @pytest.mark.usefixtures("fast_tick")
    def test_a_pause_during_a_race_holds_once_it_ends(
        self, store: Store, runner: RecordingRunner
    ) -> None:
        runner.gate = threading.Event()
        scheduler = Scheduler(store, runner=runner, interval=0.05)
        scheduler.start()
        try:
            assert runner.started.wait(5.0), "the cadence never fired a race"
            scheduler.pause()
            runner.gate.set()
            _wait_for(lambda: len(store.scoring_records()) == 1)
            time.sleep(_SEVERAL_BOUNDARIES)
        finally:
            runner.gate.set()
            scheduler.stop()
        assert len(runner.calls) == 1

    @pytest.mark.usefixtures("fast_tick")
    def test_a_resume_during_a_race_gives_a_full_interval_from_the_resume(
        self, store: Store, runner: RecordingRunner
    ) -> None:
        """Not the next boundary on the race's own phase, which the loop
        would otherwise set once the race ends."""
        runner.gate = threading.Event()
        scheduler = Scheduler(store, runner=runner, interval=0.05)
        scheduler.start()
        try:
            assert runner.started.wait(5.0), "the cadence never fired a race"
            # Long, so no second race muddies the reading, and the race's own
            # phase falls a clear `held` behind a full interval from now.
            scheduler.interval = 10.0
            held = 0.2
            time.sleep(held)
            scheduler.pause()
            scheduler.resume()
            runner.gate.set()
            _wait_for(lambda: len(store.scoring_records()) == 1)
            remaining = scheduler.seconds_to_next_race
        finally:
            runner.gate.set()
            scheduler.stop()
        assert remaining is not None
        assert remaining > scheduler.interval - held / 2


class TestNextBoundary:
    def test_a_race_inside_its_slot_advances_by_one_interval(self) -> None:
        assert next_boundary(1000.0, 300.0, now=1100.0) == 1300.0

    def test_an_overrun_skips_the_boundaries_it_ate(self) -> None:
        """The bug this covers: a race that outlasted its slot left the next
        boundary in the past, so the cadence collapsed into back-to-back
        races instead of resuming on its own rhythm."""
        nxt = next_boundary(1000.0, 300.0, now=1700.0)
        assert nxt == 1900.0
        assert nxt > 1700.0

    def test_the_phase_survives_any_overrun(self) -> None:
        for now in (1000.1, 1500.0, 4321.0):
            nxt = next_boundary(1000.0, 300.0, now)
            assert (nxt - 1000.0) % 300.0 == pytest.approx(0.0, abs=1e-6)
            assert nxt > now
