"""The rendered pages, without a web server in the way.

The pages are the only entry points — the sign-in, the sign-up and the
leaderboard, admin and live tabs in `views`, the push and replays tabs in
`team_views` — and everything else on a page is a helper of it, so these
build the event state through the store and the scheduler and read the
whole page back.
`_section` pulls one board out of it, so a test about the official table is
not fooled by a name that also appears on the qualification table above.
"""

import re
import threading
import time
from pathlib import Path

import pytest
from cocoracer.race_state import VehicleStatus

from cocoracer_server import runner, team_views
from cocoracer_server.scheduler import Scheduler
from cocoracer_server.scoring import RaceKind, RaceRecord
from cocoracer_server.store import RacedCar, RaceReport, Store
from cocoracer_server.team_views import push_page, race_page, replays_page
from cocoracer_server.views import (
    Viewer,
    admin_page,
    leaderboard_page,
    live_page,
    live_url,
    login_page,
    signup_page,
)
from tests.conftest import CAR, RecordingRunner, enter, key

ADMIN = Viewer()


def page(store: Store, scheduler: Scheduler, admin: bool) -> str:
    """The admin's controls tab, or the leaderboard as the admin sees it."""
    if admin:
        return admin_page(store, scheduler)
    return leaderboard_page(store, scheduler, ADMIN)


def _team(store: Store, name: str = "alpha") -> Viewer:
    team = store.account(name)
    assert team is not None
    return Viewer(team)


@pytest.fixture
def teams() -> tuple[str, ...]:
    return ("alpha", "bravo")


def _section(body: str, heading: str) -> str:
    """The one section under `heading`; its absence is an error, not an empty string."""
    start = body.index(f"<h2>{heading}</h2>")
    return body[start : body.index("</section>", start)]


def _by_id(body: str, section: str) -> str:
    """The one section with id `section`, for the sections with no heading."""
    start = body.index(f'<section id="{section}"')
    return body[start : body.index("</section>", start)]


def _record(
    store: Store,
    grid: list[str],
    classified: list[str],
    kind: RaceKind = RaceKind.QUALIFICATION,
    faults: dict[str, str] | None = None,
    void_reason: str | None = None,
    statuses: dict[str, str] | None = None,
    qualification_points: dict[str, int] | None = None,
    versions: dict[str, int] | None = None,
    finished_at: float | None = None,
) -> None:
    """Record a race, finished now unless `finished_at` says otherwise, of
    the teams already signed up under the grid's names, each car racing the
    version `versions` gives it, or its team's latest. A name no team has
    now enters a car no team owns."""
    teams = {n: store.account(n) for n in grid}
    cars = {
        n: RacedCar(team.registered_at, (versions or {}).get(n, team.pushes))
        for n, team in teams.items()
        if team is not None
    }
    store.record_race(
        RaceReport(
            record=RaceRecord(kind, tuple(grid), tuple(classified)),
            track="stadium",
            laps=1,
            finished_at=time.time() if finished_at is None else finished_at,
            statuses=statuses or {},
            faults=faults or {},
            void=void_reason is not None,
            void_reason=void_reason,
            qualification_points=qualification_points,
            cars=cars,
        )
    )


def _headers(table_html: str) -> list[str]:
    """The header cells of the tables in a piece of page, in order."""
    return re.findall(r"<th(?:\s[^>]*)?>(.*?)</th>", table_html)


def _cells(row_html: str) -> list[str]:
    """The text of each cell in one table row, markup stripped."""
    return [
        re.sub(r"<[^>]+>", "", c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html)
    ]


def _row(table_html: str, team: str) -> list[str]:
    """The cells of the row naming `team`."""
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S):
        if f"<td>{team}</td>" in row:
            return _cells(row)
    raise AssertionError(f"no row for {team}")


class TestLoginPage:
    def test_one_form_for_a_team_or_the_admin(self) -> None:
        body = login_page()
        assert body.count("<form") == 1 and 'action="/login"' in body
        assert 'name="role" value="team"' in body
        assert 'name="role" value="admin"' in body
        assert 'name="team"' in body and 'name="password"' in body
        assert 'href="/signup"' in body and "Créer une équipe" in body

    def test_it_shows_nothing_of_the_event(self) -> None:
        body = login_page()
        assert "Classement Qualif" not in body and "id='status'" not in body

    def test_a_refused_team_gets_its_name_back(self) -> None:
        body = login_page(error="Non.", name="alpha")
        assert "Non." in body and "value='alpha'" in body

    def test_a_refused_admin_stays_on_the_admin_side(self) -> None:
        body = login_page(role="admin", error="Non.")
        admin = body[body.index('value="admin"') :]
        assert admin.split(">", 1)[0].split() == ['value="admin"', "checked"]


class TestSignupPage:
    def test_it_creates_a_team(self) -> None:
        body = signup_page()
        assert 'action="/signup"' in body and "Créer l'équipe" in body
        assert 'href="/"' in body

    def test_a_refused_name_comes_back(self) -> None:
        body = signup_page(error="Pris.", name="alpha")
        assert "Pris." in body and "value='alpha'" in body


class TestTabs:
    def test_no_tab_reloads_itself(self, store: Store, scheduler: Scheduler) -> None:
        account = store.account("alpha")
        assert account is not None
        for body in (
            page(store, scheduler, admin=False),
            page(store, scheduler, admin=True),
            push_page(store, scheduler, account),
        ):
            assert 'http-equiv="refresh"' not in body
            assert "id='status'" in body

    def test_only_the_admin_tab_carries_the_controls(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        assert "Start an official race" not in page(store, scheduler, admin=False)
        assert "Start an official race" in page(store, scheduler, admin=True)

    def test_both_boards_are_there_before_any_official_race(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        body = page(store, scheduler, admin=False)
        board = _section(body, "Classement courses officielles")
        assert "Aucune course officielle pour l'instant." in board
        assert "alpha" in _section(body, "Classement Qualif")

    def test_the_official_board_comes_first(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        """It decides the challenge, before and after the first official race."""
        before = page(store, scheduler, admin=False)
        _record(store, ["alpha", "bravo"], ["bravo", "alpha"], RaceKind.OFFICIAL)
        after = page(store, scheduler, admin=False)
        for body in (before, after):
            official = body.index("<h2>Classement courses officielles</h2>")
            assert official < body.index("<h2>Classement Qualif</h2>")

    def test_every_registered_team_is_listed_before_it_has_raced(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        body = page(store, scheduler, admin=False)
        assert "alpha" in body and "bravo" in body

    def test_the_team_line_is_picked_out(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        body = leaderboard_page(store, scheduler, _team(store))
        assert "<tr class='me'><td>1</td><td>alpha</td>" in body
        assert "class='me'" not in page(store, scheduler, admin=False)


class TestLiveTab:
    def test_headless_it_says_there_is_no_live_view(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        body = live_page(scheduler, _team(store), "example.org")
        assert "Pas de course en direct" in body
        assert "ReplayPlayer.live(" not in body

    def test_live_it_mounts_the_live_player(self, store: Store) -> None:
        scheduler = Scheduler(store, live=True, port=8123)
        body = live_page(scheduler, _team(store), "example.org")
        assert "ReplayPlayer.live(" in body and "/static/replay.js" in body
        assert "data-ws='ws://example.org:8123/ws'" in body
        assert "aria-current='page'>Live</a>" in body
        assert "<iframe" not in body

    def test_a_team_starts_with_its_own_car_circled(self, store: Store) -> None:
        """The player gets the team's name to circle; the admin and an
        anonymous viewer, the projector, get none."""
        scheduler = Scheduler(store, live=True, port=8123)
        body = live_page(scheduler, _team(store), "example.org")
        assert 'ReplayPlayer.live(el, el.dataset.ws, {"me": "alpha"});' in body
        for viewer in (ADMIN, None):
            body = live_page(scheduler, viewer, "example.org")
            assert "ReplayPlayer.live(el, el.dataset.ws);" in body
            assert '"me"' not in body

    def test_its_status_polls_every_second_and_the_other_tabs_every_five(
        self, store: Store
    ) -> None:
        """The Live tab's player connects when the status sees a race run,
        so there the status asks every second; elsewhere every five."""
        scheduler = Scheduler(store, live=True, port=8123)
        live = _status(live_page(scheduler, _team(store), "example.org"))
        assert "data-poll='1'" in live
        board = _status(leaderboard_page(store, scheduler, _team(store)))
        assert "data-poll='5'" in board

    def test_an_ipv6_host_is_bracketed(self) -> None:
        assert live_url("::1", 8123) == "ws://[::1]:8123/ws"
        assert live_url("10.0.0.2", 8123) == "ws://10.0.0.2:8123/ws"

    def test_behind_the_proxy_it_is_on_the_page_origin(self) -> None:
        proxy = "https://name.example.org"
        assert live_url("name.example.org", 8123, proxy) == (
            "wss://name.example.org/live/ws"
        )
        assert live_url("10.0.0.2", 8123, "http://10.0.0.2:8443") == (
            "ws://10.0.0.2:8443/live/ws"
        )


def _status(body: str) -> str:
    start = body.index("<p class='status'")
    return body[start : body.index("</p>", start)]


class TestStatus:
    def test_it_counts_down_to_the_next_qualification(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        scheduler.interval = 192.5
        scheduler.resume()
        status = _status(page(store, scheduler, admin=False))
        assert "prochaine qualif dans <strong>3:12</strong>" in status
        assert "data-next='19" in status

    def test_a_paused_cadence_replaces_the_countdown(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        scheduler.resume()
        scheduler.pause()
        status = _status(page(store, scheduler, admin=False))
        assert "qualifications en pause" in status
        assert "prochaine qualif" not in status

    def test_it_is_the_same_for_a_team_and_the_admin(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        scheduler.pause()
        team = _status(leaderboard_page(store, scheduler, _team(store)))
        assert team == _status(page(store, scheduler, admin=False))

    def test_the_admin_tab_says_it_in_french_like_every_other_tab(
        self, store: Store, scheduler: Scheduler, runner: RecordingRunner
    ) -> None:
        """The status is the one French thing in the Admin tab's header."""
        scheduler.interval = 192.5
        scheduler.resume()
        status = _status(page(store, scheduler, admin=True))
        assert "prochaine qualif dans <strong>3:12</strong>" in status
        scheduler.pause()
        assert _status(page(store, scheduler, admin=True)) == _status(
            page(store, scheduler, admin=False)
        )
        assert "qualifications en pause" in _status(page(store, scheduler, admin=True))
        for race, words in (
            (scheduler.run_qualification, "course de qualification en cours"),
            (scheduler.run_official, "course officielle en cours"),
        ):
            runner.started.clear()
            runner.gate = threading.Event()
            running = threading.Thread(target=race)
            running.start()
            try:
                assert runner.started.wait(5.0)
                admin, team = (
                    _status(page(store, scheduler, a)) for a in (True, False)
                )
                assert words in admin and admin == team
            finally:
                runner.gate.set()
                running.join(5.0)

    def test_the_admin_tab_s_script_carries_no_english_status(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        body = page(store, scheduler, admin=True)
        assert "qualification paused" not in body
        assert "race running" not in body and "next qualification" not in body

    def test_there_is_no_event_summary_line(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        _record(store, ["alpha", "bravo"], ["bravo", "alpha"])
        store.lock_signups()
        body = page(store, scheduler, admin=False)
        assert "équipes" not in body and "course disputée" not in body
        assert "inscriptions" not in body

    def test_no_page_has_a_hint(self, store: Store, scheduler: Scheduler) -> None:
        account = store.account("alpha")
        assert account is not None
        _record(store, ["alpha"], ["alpha"])
        for body in (
            login_page(),
            signup_page(),
            page(store, scheduler, admin=False),
            page(store, scheduler, admin=True),
            push_page(store, scheduler, account),
            replays_page(store, scheduler, ADMIN),
            replays_page(store, scheduler, _team(store)),
            race_page(store, scheduler, ADMIN, store.races()[0]),
        ):
            assert 'class="hint"' not in body and "class='hint'" not in body

    def test_the_title_is_the_event_s(self, store: Store, scheduler: Scheduler) -> None:
        body = page(store, scheduler, admin=False)
        assert "Défi vroom vroom" in body and "CS Games" not in body
        assert "Défi vroom vroom" in login_page()


class TestPushTab:
    def test_push_is_never_disabled(self, store: Store, scheduler: Scheduler) -> None:
        # Not by an official race, which races the sources it snapshotted,
        # nor by locked sign-ups, which only refuse new teams.
        _record(store, ["alpha"], ["alpha"], RaceKind.OFFICIAL)
        store.lock_signups()
        account = store.account("alpha")
        assert account is not None
        body = push_page(store, scheduler, account)
        assert "disabled" not in body.split('<section class="push">')[1]


def _push_tab(store: Store, scheduler: Scheduler, refused: str | None = None) -> str:
    account = store.account("alpha")
    assert account is not None
    return push_page(store, scheduler, account, refused)


def _version_rows(body: str) -> list[list[str]]:
    """The cells of each row of the push tab's versions table, in order."""
    table = _section(body, "Versions")
    return [_cells(row) for row in re.findall(r"<tr>(.*?)</tr>", table, re.S)[1:]]


class TestVersionsTable:
    def test_newest_first_with_the_latest_racing_next(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        store.push(key(store, "alpha"), CAR + "# v2\n")
        store.push(key(store, "alpha"), CAR + "# v3\n")
        table = _section(_push_tab(store, scheduler), "Versions")
        assert _headers(table) == ["Version", "Heure", "Résultats", "Description", ""]
        rows = _version_rows(_push_tab(store, scheduler))
        assert [row[0] for row in rows] == ["v3 in use", "v2", "v1"]
        assert table.count("in use") == 1
        assert "en course" not in table
        assert [row[4] for row in rows] == ["Pull", "Pull", "Pull"]

    def test_each_description_is_an_escaped_form_of_its_own(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        store.describe(key(store, "alpha"), 1, 'une "<b>bonne</b>" idée')
        table = _section(_push_tab(store, scheduler), "Versions")
        assert '<form method="post" action="/versions/1/description"' in table
        assert 'value="une &quot;&lt;b&gt;bonne&lt;/b&gt;&quot; idée"' in table
        assert "<b>bonne</b>" not in table
        assert ">Enregistrer</button>" in table

    def test_results_read_as_a_count_and_the_best_finish(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        # What the counts are is the store's (`Store.version_results`); this
        # is how they read. v1: two races, best P1. v2: one race, never
        # classified. v3 never raced.
        now = time.time()
        store.push(key(store, "alpha"), CAR + "# v2\n")
        store.push(key(store, "alpha"), CAR + "# v3\n")
        both = ["alpha", "bravo"]
        on = {"alpha": 1, "bravo": 1}
        _record(store, both, ["bravo", "alpha"], versions=on, finished_at=now)
        _record(store, both, both, versions=on, finished_at=now)
        _record(store, both, ["bravo"], versions={"alpha": 2}, finished_at=now)
        rows = _version_rows(_push_tab(store, scheduler))
        assert [row[2] for row in rows] == [
            "aucune course",
            "1 course",
            "2 courses, meilleure P1",
        ]
        assert "href='/replays'" in _section(_push_tab(store, scheduler), "Versions")

    def test_a_refused_description_shows_over_the_table(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        body = _push_tab(store, scheduler, "Version <inconnue>.")
        assert "Version &lt;inconnue&gt;." in _section(body, "Versions")

    def test_there_is_no_table_before_the_first_push(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        store.register("charlie", "pw")
        account = store.account("charlie")
        assert account is not None
        assert "Versions" not in push_page(store, scheduler, account)


class TestAdminControls:
    def test_a_running_cadence_offers_the_pause(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        assert "Pause qualification" in page(store, scheduler, admin=True)

    def test_a_paused_cadence_offers_the_resume(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        scheduler.pause()
        assert "Resume qualification" in page(store, scheduler, admin=True)

    def test_team_creation_is_offered_locked_or_unlocked(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        body = page(store, scheduler, admin=True)
        assert "Lock team creation" in body and "Unlock" not in body
        store.lock_signups()
        body = page(store, scheduler, admin=True)
        assert "Unlock team creation" in body and "Lock team" not in body

    def test_every_team_can_be_deleted(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        body = page(store, scheduler, admin=True)
        assert "Delete alpha" in body and "Delete bravo" in body

    def test_the_admin_tab_is_english_header_included(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        store.lock_signups()
        body = page(store, scheduler, admin=True)
        assert '<html lang="en">' in body
        assert "Sign out" in body and "Se déconnecter" not in body
        assert "Delete a team" in body

    def test_the_other_tabs_stay_french_for_the_admin(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        body = page(store, scheduler, admin=False)
        assert '<html lang="fr">' in body and "Se déconnecter" in body
        # "admin", never "administration", in either language.
        assert "<strong>Admin</strong>" in body
        assert "Administration" not in body and "Administration" not in login_page()


class TestQualificationTable:
    @pytest.fixture
    def teams(self) -> tuple[str, ...]:
        return ("alpha", "bravo", "charlie")

    def test_the_most_positions_gained_leads(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        grid = ["alpha", "bravo", "charlie"]
        _record(store, grid, ["bravo", "charlie", "alpha"])  # bravo +1, charlie +1
        _record(store, grid, ["bravo", "alpha", "charlie"])  # bravo +1
        board = _section(page(store, scheduler, admin=False), "Classement Qualif")
        assert board.index("bravo") < board.index("charlie") < board.index("alpha")


class TestQualificationTableBeforeAnyoneRegisters:
    @pytest.fixture
    def teams(self) -> tuple[str, ...]:
        return ()

    def test_an_empty_field_says_nothing_yet(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        board = _section(page(store, scheduler, admin=False), "Classement Qualif")
        assert "Rien pour l'instant." in board


class TestOfficialTable:
    def test_it_shows_every_result_and_their_sum(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        grid = ["alpha", "bravo"]
        _record(store, grid, ["alpha", "bravo"], RaceKind.OFFICIAL)
        _record(store, grid, ["bravo", "alpha"], RaceKind.OFFICIAL)
        _record(store, grid, ["alpha", "bravo"], RaceKind.OFFICIAL)
        board = _section(
            page(store, scheduler, admin=False), "Classement courses officielles"
        )
        assert '<th class="num">Résultats</th>' in board
        assert '<th class="num">Points</th>' in board
        assert "Moyenne" not in board and "Meilleure" not in board
        assert (
            "<td>alpha</td><td class='num'>1, 2, 1</td><td class='num'>4</td>" in board
        )
        assert (
            "<td>bravo</td><td class='num'>2, 1, 2</td><td class='num'>5</td>" in board
        )

    def test_a_missed_race_reads_as_a_dash_and_costs_last_plus_one(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        _record(store, ["alpha"], ["alpha"], RaceKind.OFFICIAL)
        _record(store, ["alpha", "bravo"], ["bravo", "alpha"], RaceKind.OFFICIAL)
        board = _section(
            page(store, scheduler, admin=False), "Classement courses officielles"
        )
        assert "<td>bravo</td><td class='num'>—, 1</td><td class='num'>3</td>" in board

    def test_a_void_official_race_counts_for_nobody(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        _record(store, ["alpha", "bravo"], ["alpha", "bravo"], RaceKind.OFFICIAL)
        _record(store, ["alpha", "bravo"], [], RaceKind.OFFICIAL, void_reason="stuck")
        board = _section(
            page(store, scheduler, admin=False), "Classement courses officielles"
        )
        assert "<td>alpha</td><td class='num'>1</td><td class='num'>1</td>" in board

    def test_a_team_with_no_official_race_is_left_off(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        _record(store, ["alpha"], ["alpha"], RaceKind.OFFICIAL)
        body = page(store, scheduler, admin=False)
        board = _section(body, "Classement courses officielles")
        assert "alpha" in board
        assert "bravo" not in board
        assert "bravo" in _section(body, "Classement Qualif")


class TestReplaysTab:
    def test_its_race_list_refreshes_itself(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        assert 'id="history" data-live' in replays_page(store, scheduler, _team(store))
        assert 'id="races" data-live' in replays_page(store, scheduler, ADMIN)

    def test_the_admin_sees_every_race_with_its_field(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        _record(store, ["bravo", "alpha"], ["alpha", "bravo"])
        _record(store, ["bravo"], [], void_reason="stuck")
        races = _by_id(replays_page(store, scheduler, ADMIN), "races")
        assert "<th>Premier</th>" in races
        assert races.index("/replays/2") < races.index("/replays/1")
        assert "<td>bravo, alpha</td><td>alpha</td>" in races
        assert "annulée" in races

    def test_the_history_shows_a_fault_and_a_void_race(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        _record(
            store,
            ["alpha", "bravo"],
            ["bravo", "alpha"],
            faults={"alpha": "ZeroDivisionError: division by zero"},
            statuses={"alpha": "dnf", "bravo": "finished"},
        )
        _record(store, ["alpha", "bravo"], [], void_reason="stuck")
        history = _by_id(replays_page(store, scheduler, _team(store)), "history")
        assert history.index("/replays/2") < history.index("/replays/1")
        assert "DNF" in history and "ZeroDivisionError" in history
        assert "annulée" in history

    def test_the_history_shows_the_signed_gain(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        _record(
            store,
            ["alpha", "bravo"],
            ["bravo", "alpha"],
            statuses={"alpha": "done", "bravo": "done"},
        )
        history = _by_id(replays_page(store, scheduler, _team(store)), "history")
        assert _headers(history) == [
            "Course",
            "Type",
            "Heure",
            "Version",
            "Position de départ",
            "Position d'arrivée",
            "Gains",
            "Statut",
            "",
        ]
        row = re.search(r"<tr><td><a href='/replays/1'>.*?</tr>", history)
        assert row is not None
        assert _cells(row.group())[4:8] == ["1", "2", "-1", "terminé"]

    def test_the_history_gives_the_version_each_race_ran(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        scheduler.run_qualification()
        store.push(key(store, "alpha"), CAR + "\n# v2\n", "<b>tardif</b>")
        scheduler.run_qualification()
        history = _by_id(replays_page(store, scheduler, _team(store)), "history")
        rows = re.findall(r"<tr><td><a href='/replays/\d+'>.*?</tr>", history)
        # Newest first: v2, then v1.
        assert [_cells(row)[3] for row in rows] == ["v2", "v1"]
        # The label only: the description is on the race page.
        assert "tardif" not in history

    def test_a_team_that_did_not_race_has_no_history(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        _record(store, ["bravo"], ["bravo"])
        history = _by_id(replays_page(store, scheduler, _team(store)), "history")
        assert "Rien pour l'instant." in history


class TestRacePage:
    def test_it_gives_the_race_and_its_result(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        _record(
            store,
            ["alpha", "bravo"],
            ["bravo", "alpha"],
            statuses={"alpha": "finished", "bravo": "finished"},
        )
        body = race_page(store, scheduler, ADMIN, store.races()[0])
        assert "Course n° 1" in body
        result = _section(body, "Résultat")
        assert result.index("bravo") < result.index("alpha")

    @pytest.mark.parametrize("kind", list(RaceKind))
    def test_the_result_gives_the_start_and_the_signed_gain(
        self, store: Store, scheduler: Scheduler, kind: RaceKind
    ) -> None:
        _record(
            store,
            ["alpha", "bravo", "charlie"],
            ["charlie", "bravo", "alpha"],
            kind=kind,
            statuses={"alpha": "dnf", "bravo": "done", "charlie": "finished"},
        )
        result = _section(
            race_page(store, scheduler, ADMIN, store.races()[0]), "Résultat"
        )
        assert _headers(result) == [
            "#",
            "Équipe",
            "Position de départ",
            "Gains",
            "Statut",
        ]
        assert _row(result, "charlie") == ["1", "charlie", "3", "+2", "terminé"]
        assert _row(result, "bravo") == ["2", "bravo", "2", "0", "terminé"]
        assert _row(result, "alpha") == ["3", "alpha", "1", "-2", "DNF"]

    def test_an_official_result_shows_the_points_the_grid_was_seeded_from(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        _record(
            store,
            ["alpha", "bravo"],
            ["bravo", "alpha"],
            kind=RaceKind.OFFICIAL,
            qualification_points={"alpha": 4, "bravo": 1},
        )
        result = _section(
            race_page(store, scheduler, ADMIN, store.races()[0]), "Résultat"
        )
        assert _headers(result) == [
            "#",
            "Équipe",
            "Position de départ",
            "Points de qualification",
            "Gains",
            "Statut",
        ]
        assert _row(result, "alpha")[:5] == ["2", "alpha", "1", "4", "-1"]

    @pytest.mark.parametrize("void_reason", [None, "stuck"])
    def test_it_has_no_line_of_facts_under_the_heading(
        self, store: Store, scheduler: Scheduler, void_reason: str | None
    ) -> None:
        _record(store, ["alpha"], ["alpha"], void_reason=void_reason)
        body = race_page(store, scheduler, ADMIN, store.races()[0])
        assert "race-facts" not in body and "terminée à" not in body

    def test_a_void_race_says_why_in_french(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        _record(store, ["alpha"], [], void_reason=runner.NO_ENTRIES)
        body = race_page(store, scheduler, ADMIN, store.races()[0])
        assert "Course annulée : elle n'a pas de reprise" in body
        assert "<span class='err'>aucune équipe au départ</span>" in body

    def test_a_team_reads_the_version_its_car_raced(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        store.push(key(store, "alpha"), CAR + "\n# v2\n", "freinage <tardif>")
        scheduler.run_qualification()
        store.push(key(store, "alpha"), CAR + "\n# v3\n", "plus tard")
        body = race_page(store, scheduler, _team(store), store.races()[0])
        assert (
            "Ta version : <a href='/pull?version=2' download>v2</a>"
            " — freinage &lt;tardif&gt;"
        ) in body
        assert "plus tard" not in body

    def test_a_version_without_a_description_is_just_its_label(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        scheduler.run_qualification()
        body = race_page(store, scheduler, _team(store), store.races()[0])
        assert "Ta version : <a href='/pull?version=1' download>v1</a></p>" in body

    def test_an_edited_description_shows(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        scheduler.run_qualification()
        store.describe(key(store, "alpha"), 1, "ligne droite")
        body = race_page(store, scheduler, _team(store), store.races()[0])
        assert "v1</a> — ligne droite" in body

    def test_nobody_else_reads_a_team_s_version(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        store.describe(key(store, "bravo"), 1, "secret de bravo")
        scheduler.run_qualification()
        mine = race_page(store, scheduler, _team(store), store.races()[0])
        assert "secret de bravo" not in mine and mine.count("Ta version") == 1
        admin = race_page(store, scheduler, ADMIN, store.races()[0])
        assert "Ta version" not in admin and "secret de bravo" not in admin

    def test_a_team_that_did_not_race_reads_no_version(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        enter(store, "charlie")
        _record(
            store, ["bravo"], ["bravo"], versions={"bravo": 1}, finished_at=time.time()
        )
        body = race_page(store, scheduler, _team(store, "charlie"), store.races()[0])
        assert "Ta version" not in body

    def test_a_name_taken_again_after_the_race_reads_no_version(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        scheduler.run_qualification()
        store.delete("alpha")
        enter(store, "alpha")
        body = race_page(store, scheduler, _team(store), store.races()[0])
        assert "Ta version" not in body
        history = _by_id(replays_page(store, scheduler, _team(store)), "history")
        assert "v1" not in history

    def test_the_page_is_wide_enough_for_the_player(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        _record(store, ["alpha"], ["alpha"])
        body = race_page(store, scheduler, _team(store), store.races()[0])
        assert "<main class='wide'>" in body


class TestStatusLabels:
    LABELS = {
        "racing": "en course",
        "paused": "pause",
        "ghost": "ghost",
        "finished": "terminé",
        "done": "terminé",
        "dnf": "DNF",
    }

    def test_the_tables_use_the_decided_labels(self) -> None:
        assert team_views._STATUSES == self.LABELS

    def test_every_status_a_replay_records_has_a_label(self) -> None:
        assert set(self.LABELS) == {s.value for s in VehicleStatus}

    def test_the_replay_player_uses_the_same_labels_and_the_tower_its_tags(
        self,
    ) -> None:
        script = (Path(team_views.__file__).parent / "static" / "replay.js").read_text(
            encoding="utf-8"
        )

        def table(name: str) -> dict[str, str]:
            match = re.search(rf"const {name} = \{{(.*?)\}};", script, re.S)
            assert match is not None, name
            return dict(re.findall(r'(\w+): "([^"]*)"', match.group(1)))

        assert table("STATUS_LABELS") == self.LABELS
        assert table("STATUS_TAGS") == {
            "paused": "PAUSE",
            "ghost": "GHOST",
            "dnf": "DNF",
        }


class TestSharedHeaders:
    """A fact shown in more than one table reads the same in every one."""

    # Every header any table may use. A new table picks from these; a new
    # word for a fact already here is drift.
    VOCABULARY = {
        "#",
        "Équipe",
        "Position de départ",
        "Position d'arrivée",
        "Gains",
        "Statut",
        "Points de qualification",
        "Premier",
        "Course",
        "Type",
        "Heure",
        "Version",
        "Participants",
        "Résultats",
        "Description",
        "Points",
        "",
    }

    def test_the_shared_facts_have_their_decided_headers(self) -> None:
        assert (
            team_views.RANK,
            team_views.TEAM,
            team_views.START,
            team_views.POSITION,
            team_views.GAIN,
            team_views.STATUS,
        ) == (
            "#",
            "Équipe",
            "Position de départ",
            "Position d'arrivée",
            "Gains",
            "Statut",
        )

    def test_every_table_draws_its_headers_from_the_vocabulary(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        _record(store, ["alpha", "bravo"], ["bravo", "alpha"])
        _record(
            store,
            ["alpha", "bravo"],
            ["bravo", "alpha"],
            kind=RaceKind.OFFICIAL,
            qualification_points={"alpha": 1, "bravo": 0},
        )
        pages = [
            leaderboard_page(store, scheduler, _team(store)),
            replays_page(store, scheduler, _team(store)),
            replays_page(store, scheduler, ADMIN),
            _push_tab(store, scheduler),
            *(race_page(store, scheduler, ADMIN, r) for r in store.races()),
        ]
        headers = {h for body in pages for h in _headers(body)}
        assert headers <= self.VOCABULARY, headers - self.VOCABULARY


class TestStateLine:
    def test_an_in_memory_event_says_a_restart_erases_it(
        self, store: Store, scheduler: Scheduler
    ) -> None:
        assert "In memory only: a restart erases the event" in page(
            store, scheduler, admin=True
        )

    def test_a_saved_event_says_when_and_whether_it_was_restored(
        self, tmp_path: Path, scheduler: Scheduler
    ) -> None:
        Store(state_dir=tmp_path).register("alpha", "pw")
        store = Store(state_dir=tmp_path)
        html = page(store, scheduler, admin=True)
        assert "State saved at" in html
        assert "Restored from disk at" in html
        assert "In memory only" not in html
        assert "State saved at" not in page(store, scheduler, admin=False)


class TestReusedName:
    """A team that takes a deleted team's name starts clean on every page:
    the old team's races are history, not the new team's."""

    @pytest.fixture
    def retaken(self, store: Store, scheduler: Scheduler) -> Viewer:
        """A qualification (bravo +1) and an official race, then bravo
        deleted and its name taken by a new team; the new team's view."""
        scheduler.run_qualification()
        scheduler.run_official()
        store.delete("bravo")
        enter(store, "bravo", password="other")
        return _team(store, "bravo")

    @pytest.mark.parametrize("admin", [False, True])
    def test_both_boards_show_it_with_nothing(
        self, store: Store, scheduler: Scheduler, retaken: Viewer, admin: bool
    ) -> None:
        body = leaderboard_page(store, scheduler, retaken if not admin else ADMIN)
        qualification = _section(body, "Classement Qualif")
        assert _row(qualification, "bravo")[2] == "0"
        official = _section(body, "Classement courses officielles")
        assert "bravo" not in official
        # alpha's line is as the races left it: P1 in a field of two.
        assert _row(official, "alpha")[2:] == ["1", "1"]

    def test_its_replay_list_is_empty(
        self, store: Store, scheduler: Scheduler, retaken: Viewer
    ) -> None:
        history = _by_id(replays_page(store, scheduler, retaken), "history")
        assert "Rien pour l'instant." in history

    def test_the_old_races_are_not_its_own(
        self, store: Store, scheduler: Scheduler, retaken: Viewer
    ) -> None:
        for race in store.races():
            body = race_page(store, scheduler, retaken, race)
            assert "/logs" not in body and '"me"' not in body
            assert "Ta version" not in body
            # The old team's car is still there, under its name.
            assert "bravo" in _section(body, "Résultat")

    def test_the_other_team_still_sees_its_own_car(
        self, store: Store, scheduler: Scheduler, retaken: Viewer
    ) -> None:
        body = race_page(store, scheduler, _team(store, "alpha"), store.races()[0])
        assert '"me": "alpha"' in body and "Ta version" in body
