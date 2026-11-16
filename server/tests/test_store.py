"""Sign-up, sign-in, gated pushes, the team cap, the sign-up lock, deletion, and
the state file."""

import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from cocoracer_server import store as store_module
from cocoracer_server.__main__ import main
from cocoracer_server.scoring import RaceKind, RaceRecord
from cocoracer_server.store import (
    CONTROLLER_DIR,
    LOG_DIR,
    MAX_DESCRIPTION_CHARS,
    MAX_NAME_CHARS,
    MAX_SOURCE_BYTES,
    REPLAY_DIR,
    STATE_FILE,
    PushRejected,
    RacedCar,
    RaceReport,
    Rejected,
    SignedOut,
    StateError,
    Store,
    Team,
    TeamKey,
    VersionResults,
    hash_password,
    password_matches,
    validate_name,
)
from tests.conftest import CAR, enter, key

# Passes analysis with one warning: importlib.
WARNED = "import importlib\n" + CAR
# Fails analysis: there is no Controller subclass at all.
BROKEN = "x = 1\n"
# A replay as the runner hands it over: already serialised.
REPLAY = b'{"static":{},"names":["a"],"hz":10,"frames":[{"t":0.0}]}'
# The cars' logs as the runner hands them over: by team.
LOGS = {"a": {"lines": [[0.0, "loaded"], [3.5, "lap"]], "truncated": False}}


def _team(store: Store, name: str) -> Team:
    team = store.account(name)
    assert team is not None
    return team


def _report(
    store: Store,
    grid: list[str],
    classified: list[str],
    versions: dict[str, int] | None = None,
) -> RaceReport:
    """A race finished now, of the teams signed up under the grid's names,
    each car racing the version `versions` gives it, or its team's latest.
    A name no team has now enters a car no team owns."""
    teams = {name: store.account(name) for name in grid}
    return RaceReport(
        record=RaceRecord(RaceKind.QUALIFICATION, tuple(grid), tuple(classified)),
        track="stadium",
        laps=1,
        finished_at=time.time(),
        statuses={},
        faults={},
        cars={
            name: RacedCar(team.registered_at, (versions or {}).get(name, team.pushes))
            for name, team in teams.items()
            if team is not None
        },
    )


class TestNames:
    def test_surrounding_and_repeated_whitespace_is_normalised(self) -> None:
        assert validate_name("  the   racers ") == "the racers"

    def test_tabs_and_newlines_collapse_to_single_spaces(self) -> None:
        assert validate_name("a\tb\nc") == "a b c"

    def test_an_empty_name_is_rejected(self) -> None:
        with pytest.raises(Rejected, match="ne peut pas être vide"):
            validate_name("   ")

    def test_an_overlong_name_is_rejected(self) -> None:
        with pytest.raises(Rejected, match="dépasse"):
            validate_name("x" * (MAX_NAME_CHARS + 1))

    @pytest.mark.parametrize("name", ["<script>", "team/1", "a&b", "-leading", "é"])
    def test_names_that_would_not_render_safely_are_rejected(self, name: str) -> None:
        with pytest.raises(Rejected, match="ne peut contenir que"):
            validate_name(name)


class TestSignUp:
    def test_a_new_team_has_no_submission_and_is_not_in_the_field(
        self, store: Store
    ) -> None:
        assert store.register("  team   a ", "pw").name == "team a"
        (account,) = store.accounts()
        assert account.name == "team a"
        assert not account.submitted
        assert store.teams() == []
        assert store.standings() == []

    def test_a_taken_name_is_refused(self, store: Store) -> None:
        store.register("team-a", "pw")
        with pytest.raises(Rejected, match="déjà pris") as exc:
            store.register("team-a", "other")
        assert exc.value.status_code == 409

    def test_a_password_is_required(self, store: Store) -> None:
        with pytest.raises(Rejected, match="mot de passe"):
            store.register("team-a", "")

    def test_the_seventeenth_team_is_refused(self) -> None:
        store = Store(max_teams=2)
        store.register("a", "pw")
        store.register("b", "pw")
        with pytest.raises(Rejected, match="complet") as exc:
            store.register("c", "pw")
        assert exc.value.status_code == 409

    def test_a_team_with_no_submission_still_takes_a_seat(self) -> None:
        store = Store(max_teams=1)
        store.register("a", "pw")
        with pytest.raises(Rejected, match="complet"):
            store.register("b", "pw")


class TestSignIn:
    def test_the_right_password_signs_in(self, store: Store) -> None:
        store.register("team a", "pw")
        team = store.authenticate(" team  a ", "pw")
        assert team is not None and team.name == "team a"

    def test_a_wrong_password_does_not(self, store: Store) -> None:
        store.register("team-a", "pw")
        assert store.authenticate("team-a", "PW") is None
        assert store.authenticate("team-a", "") is None

    def test_an_unknown_or_malformed_name_does_not(self, store: Store) -> None:
        assert store.authenticate("nobody", "pw") is None
        assert store.authenticate("<script>", "pw") is None

    def test_a_password_with_accents_works(self, store: Store) -> None:
        store.register("team-a", "élan")
        assert store.authenticate("team-a", "élan") is not None


class TestPasswordHash:
    def test_the_password_itself_is_never_stored(self, store: Store) -> None:
        store.register("team-a", "hunter2")
        (account,) = store.accounts()
        assert "hunter2" not in account.password_hash
        assert account.password_hash.startswith("scrypt$")

    def test_two_teams_with_one_password_get_different_hashes(
        self, store: Store
    ) -> None:
        store.register("team-a", "pw")
        store.register("team-b", "pw")
        a, b = store.accounts()
        assert a.password_hash != b.password_hash

    def test_a_hash_checks_with_the_cost_it_was_made_with(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Raising the cost later must not lock existing accounts out."""
        old = hash_password("pw")
        monkeypatch.setattr(store_module, "SCRYPT_N", 4)
        assert password_matches("pw", old)
        assert not password_matches("PW", old)
        assert hash_password("pw").split("$")[1] == "4"


class TestPush:
    def test_the_first_push_enters_the_field(self, store: Store) -> None:
        store.register("team-a", "pw")
        analysis = store.push(key(store, "team-a"), CAR)
        assert analysis.ok and analysis.warnings == ()
        (team,) = store.teams()
        assert team.source == CAR and team.pushes == 1

    def test_a_later_push_is_the_one_that_races(self, store: Store) -> None:
        enter(store, "team-a")
        store.push(key(store, "team-a"), WARNED)
        (team,) = store.teams()
        assert team.source == WARNED
        assert team.pushes == 2

    def test_a_push_with_errors_is_refused_and_the_previous_one_kept(
        self, store: Store
    ) -> None:
        enter(store, "team-a")
        with pytest.raises(PushRejected) as exc:
            store.push(key(store, "team-a"), BROKEN)
        assert exc.value.analysis is not None and exc.value.analysis.errors
        (team,) = store.teams()
        assert team.source == CAR and team.pushes == 1
        feedback = store.feedback(key(store, "team-a"))
        assert feedback is not None and not feedback.accepted
        assert "soumission précédente continue de courir" in feedback.message
        assert feedback.analysis is exc.value.analysis
        assert feedback.draft == BROKEN

    def test_a_first_push_with_errors_leaves_the_team_out_of_the_field(
        self, store: Store
    ) -> None:
        store.register("team-a", "pw")
        with pytest.raises(PushRejected):
            store.push(key(store, "team-a"), BROKEN)
        assert store.teams() == []
        feedback = store.feedback(key(store, "team-a"))
        assert feedback is not None
        assert "n'a toujours pas de soumission" in feedback.message

    def test_a_push_with_warnings_goes_through_and_keeps_them(
        self, store: Store
    ) -> None:
        store.register("team-a", "pw")
        analysis = store.push(key(store, "team-a"), WARNED)
        assert analysis.ok and analysis.warnings
        feedback = store.feedback(key(store, "team-a"))
        assert feedback is not None and feedback.accepted
        assert feedback.analysis is analysis
        assert feedback.draft is None
        assert store.teams()[0].source == WARNED

    def test_an_empty_controller_is_refused(self, store: Store) -> None:
        store.register("team-a", "pw")
        with pytest.raises(PushRejected, match="vide"):
            store.push(key(store, "team-a"), "   \n  ")

    def test_an_oversized_controller_is_refused(self, store: Store) -> None:
        store.register("team-a", "pw")
        with pytest.raises(PushRejected, match="dépasse") as exc:
            store.push(key(store, "team-a"), CAR + "#" * MAX_SOURCE_BYTES)
        assert exc.value.status_code == 413

    def test_locked_sign_ups_refuse_new_teams_but_not_pushes(
        self, store: Store
    ) -> None:
        enter(store, "team-a")
        store.lock_signups()
        with pytest.raises(Rejected, match="inscriptions sont fermées") as exc:
            store.register("team-b", "pw")
        assert exc.value.status_code == 409
        assert [t.name for t in store.accounts()] == ["team-a"]
        store.push(key(store, "team-a"), WARNED)
        assert store.teams()[0].source == WARNED
        store.unlock_signups()
        store.register("team-b", "pw")
        assert [t.name for t in store.accounts()] == ["team-a", "team-b"]

    def test_a_refusal_the_store_cannot_see_is_kept_as_feedback(
        self, store: Store
    ) -> None:
        enter(store, "team-a")
        store.refuse(key(store, "team-a"), "Le fichier n'est pas du texte UTF-8.")
        feedback = store.feedback(key(store, "team-a"))
        assert feedback is not None and not feedback.accepted
        assert feedback.message.endswith("Le fichier n'est pas du texte UTF-8.")
        assert store.teams()[0].pushes == 1


class TestVersions:
    def test_each_accepted_push_is_the_next_version(self, store: Store) -> None:
        store.register("team-a", "pw")
        store.push(key(store, "team-a"), CAR, "first lap")
        store.push(key(store, "team-a"), WARNED)
        store.push(key(store, "team-a"), CAR + "# third\n", "  faster\n  in   turns ")
        (team,) = store.teams()
        assert [v.label for v in team.versions] == ["v1", "v2", "v3"]
        assert [v.description for v in team.versions] == [
            "first lap",
            "",
            "faster in turns",
        ]
        assert team.source == CAR + "# third\n" and team.pushes == 3
        assert team.updated_at == team.versions[-1].pushed_at
        assert store.version_source(key(store, "team-a"), 1) == CAR
        assert store.version_source(key(store, "team-a"), 2) == WARNED
        assert store.version_source(key(store, "team-a"), 4) is None

    def test_the_accepted_feedback_carries_no_message(self, store: Store) -> None:
        enter(store, "team-a")
        store.push(key(store, "team-a"), WARNED)
        feedback = store.feedback(key(store, "team-a"))
        assert feedback is not None
        assert feedback.accepted
        assert feedback.message == ""

    def test_a_refused_push_uses_up_no_number(self, store: Store) -> None:
        enter(store, "team-a")
        with pytest.raises(PushRejected):
            store.push(key(store, "team-a"), BROKEN, "broken one")
        feedback = store.feedback(key(store, "team-a"))
        assert feedback is not None and feedback.description == "broken one"
        store.push(key(store, "team-a"), WARNED)
        (team,) = store.teams()
        assert [v.number for v in team.versions] == [1, 2]

    def test_an_overlong_description_is_refused_and_the_source_handed_back(
        self, store: Store
    ) -> None:
        enter(store, "team-a")
        too_long = "x" * (MAX_DESCRIPTION_CHARS + 1)
        with pytest.raises(PushRejected, match="description dépasse") as exc:
            store.push(key(store, "team-a"), WARNED, too_long)
        assert exc.value.status_code == 400
        feedback = store.feedback(key(store, "team-a"))
        assert feedback is not None and not feedback.accepted
        assert feedback.draft == WARNED and feedback.description == too_long
        assert store.teams()[0].pushes == 1
        store.push(key(store, "team-a"), WARNED, "x" * MAX_DESCRIPTION_CHARS)
        assert store.teams()[0].pushes == 2

    def test_a_description_can_be_changed_later(self, store: Store) -> None:
        enter(store, "team-a")
        store.push(key(store, "team-a"), WARNED, "old")
        store.describe(key(store, "team-a"), 1, "  the   first ")
        store.describe(key(store, "team-a"), 2, "")
        (team,) = store.teams()
        assert [v.description for v in team.versions] == ["the first", ""]
        assert team.source == WARNED
        assert store.version_source(key(store, "team-a"), 1) == CAR

    def test_describing_follows_the_same_rules(self, store: Store) -> None:
        enter(store, "team-a")
        with pytest.raises(Rejected, match="description dépasse"):
            store.describe(key(store, "team-a"), 1, "x" * (MAX_DESCRIPTION_CHARS + 1))
        with pytest.raises(Rejected) as exc:
            store.describe(key(store, "team-a"), 2, "no such version")
        assert exc.value.status_code == 404

    def test_a_deleted_team_s_versions_go_with_it(self, store: Store) -> None:
        enter(store, "team-a")
        store.delete("team-a")
        enter(store, "team-a", WARNED)
        (team,) = store.teams()
        assert [v.number for v in team.versions] == [1]
        assert store.version_source(key(store, "team-a"), 1) == WARNED


class TestDeletion:
    def test_deleting_removes_the_team_from_the_field(self, store: Store) -> None:
        enter(store, "team-a")
        gone = key(store, "team-a")
        assert store.delete("team-a") is True
        assert store.teams() == []
        assert store.accounts() == []
        assert store.feedback(gone) is None

    def test_deleting_an_unknown_team_is_reported(self, store: Store) -> None:
        assert store.delete("nobody") is False

    def test_a_deleted_name_can_be_claimed_again(self, store: Store) -> None:
        enter(store, "team-a")
        store.delete("team-a")
        assert store.register("team-a", "different").name == "team-a"
        assert store.authenticate("team-a", "pw") is None

    def test_a_deleted_team_leaves_the_standings(self, store: Store) -> None:
        for name in ("a", "b", "c"):
            enter(store, name)
        store.record_race(_report(store, ["a", "b", "c"], ["c", "b", "a"]))
        assert [s.team for s in store.standings()] == ["c", "a", "b"]
        store.delete("c")
        assert [s.team for s in store.standings()] == ["a", "b"]


class TestReusedName:
    """A team is its sign-up, not its name: one that takes a deleted team's
    name starts clean, and the deletion changes nobody else's score."""

    def _event(self, store: Store) -> None:
        """Three teams, a qualification and an official race."""
        for name in ("a", "b", "c"):
            enter(store, name)
        store.record_race(_report(store, ["a", "b", "c"], ["c", "b", "a"]))
        store.record_race(
            replace(
                _report(store, ["a", "b", "c"], ["c", "a", "b"]),
                record=RaceRecord(RaceKind.OFFICIAL, ("a", "b", "c"), ("c", "a", "b")),
            )
        )

    def test_the_new_team_has_no_points_and_the_others_keep_theirs(
        self, store: Store
    ) -> None:
        self._event(store)
        before = {s.team: s for s in store.standings()}
        assert before["c"].qualification_score == 2
        store.delete("c")
        enter(store, "c", password="other")

        after = {s.team: s for s in store.standings()}
        assert after["c"].qualification_score == 0
        assert after["c"].official_score is None
        assert after["c"].official_positions == ()
        assert after["a"] == before["a"] and after["b"] == before["b"]
        assert [s.team for s in store.standings()] == ["a", "b", "c"]

    def test_its_own_races_count(self, store: Store) -> None:
        self._event(store)
        store.delete("c")
        enter(store, "c", password="other")
        store.record_race(_report(store, ["c", "a"], ["a", "c"]))
        store.record_race(_report(store, ["a", "c"], ["c", "a"]))
        scores = {s.team: s.qualification_score for s in store.standings()}
        assert scores["c"] == 1

    def test_a_race_the_old_team_was_in_is_not_the_new_one_s(
        self, store: Store
    ) -> None:
        # Deleted and its name taken again while the race ran: the race
        # finishes after the new sign-up, but the car was the old team's.
        enter(store, "a")
        enter(store, "c")
        cars = _report(store, ["a", "c"], ["c", "a"]).cars
        store.delete("c")
        enter(store, "c", password="other")
        new = store.account("c")
        assert new is not None
        report = store.record_race(
            replace(_report(store, ["a", "c"], ["c", "a"]), cars=cars)
        )
        assert report.finished_at >= new.registered_at
        assert store.team_race(new.key, report.index) is None
        assert store.team_races(new.key) == []
        scores = {s.team: s.qualification_score for s in store.standings()}
        assert scores == {"a": 0, "c": 0}

    def test_a_race_is_the_team_s_only_if_it_is_on_the_grid(self, store: Store) -> None:
        enter(store, "a")
        team = store.account("a")
        assert team is not None
        store.record_race(_report(store, ["a"], ["a"]))
        store.record_race(_report(store, ["b"], ["b"]))
        assert [r.race.index for r in store.team_races(team.key)] == [1]

    def test_whose_cars_a_race_had_survives_a_reload(self, tmp_path: Path) -> None:
        store = Store(state_dir=tmp_path)
        self._event(store)
        restored = Store(state_dir=tmp_path)
        assert restored.races() == store.races()
        assert restored.races()[0].cars["a"].registered_at is not None


class TestAGoneTeam:
    """A per-team call takes the team's key, checked under the store's lock:
    a team deleted and its name taken again between a session's check and
    its action has the action refused, and the new team is untouched."""

    @pytest.fixture
    def stale(self, store: Store) -> TeamKey:
        """The old team's key, held across its deletion; "a" is now a new
        team's, with a version of its own."""
        enter(store, "a")
        old = key(store, "a")
        store.delete("a")
        enter(store, "a", WARNED, password="other")
        store.describe(key(store, "a"), 1, "mine")
        return old

    def _untouched(self, store: Store) -> None:
        team = store.account("a")
        assert team is not None
        assert [(v.number, v.description) for v in team.versions] == [(1, "mine")]
        assert team.source == WARNED
        assert store.feedback(team.key) is not None
        assert store.feedback(team.key).accepted  # type: ignore[union-attr]

    def test_a_push_is_refused(self, store: Store, stale: TeamKey) -> None:
        with pytest.raises(SignedOut) as exc:
            store.push(stale, CAR, "not yours")
        assert exc.value.status_code == 401
        self._untouched(store)

    def test_a_refused_push_keeps_nothing_for_the_new_team(
        self, store: Store, stale: TeamKey
    ) -> None:
        with pytest.raises(SignedOut):
            store.push(stale, BROKEN)
        with pytest.raises(SignedOut):
            store.refuse(stale, "trop gros", draft=CAR)
        self._untouched(store)

    def test_a_description_is_refused(self, store: Store, stale: TeamKey) -> None:
        with pytest.raises(SignedOut):
            store.describe(stale, 1, "not yours")
        self._untouched(store)

    def test_a_pull_is_refused(self, store: Store, stale: TeamKey) -> None:
        with pytest.raises(SignedOut):
            store.version_source(stale, 1)
        self._untouched(store)

    def test_reads_find_nothing(self, store: Store, stale: TeamKey) -> None:
        store.record_race(_report(store, ["a"], ["a"]), REPLAY, LOGS)
        assert store.team(stale) is None and store.feedback(stale) is None
        assert store.team_races(stale) == [] and store.log(1, stale) is None
        assert store.version_results(stale) == {}


class TestTeamRaces:
    """The store answers which races are a team's and how its car did."""

    def test_each_race_gives_the_slot_finish_gain_status_and_version(
        self, store: Store
    ) -> None:
        enter(store, "a")
        enter(store, "b")
        store.push(key(store, "a"), CAR + "# v2\n", "plus vite")
        store.record_race(
            replace(
                _report(store, ["a", "b"], ["b", "a"]),
                statuses={"a": "dnf", "b": "done"},
                faults={"a": "boom"},
            )
        )
        [raced] = store.team_races(key(store, "a"))
        assert (raced.race.index, raced.slot, raced.finish, raced.gain) == (1, 1, 2, -1)
        assert (raced.status, raced.fault) == ("dnf", "boom")
        assert (raced.version.label, raced.version.description) == ("v2", "plus vite")
        assert store.team_race(key(store, "a"), 1) == raced

    def test_a_void_or_unclassified_car_has_no_finish(self, store: Store) -> None:
        enter(store, "a")
        enter(store, "b")
        store.record_race(_report(store, ["a", "b"], ["b"]))
        store.record_race(
            replace(_report(store, ["a"], []), void=True, void_reason="x")
        )
        races = store.team_races(key(store, "a"))
        assert [(r.finish, r.gain) for r in races] == [(None, None), (None, None)]

    def test_oldest_first_and_only_its_own(self, store: Store) -> None:
        enter(store, "a")
        enter(store, "b")
        store.record_race(_report(store, ["a", "b"], ["a", "b"]))
        store.record_race(_report(store, ["b"], ["b"]))
        store.record_race(_report(store, ["b", "a"], ["a", "b"]))
        assert [r.race.index for r in store.team_races(key(store, "a"))] == [1, 3]
        assert store.team_race(key(store, "a"), 2) is None
        assert store.team_race(key(store, "a"), 9) is None

    def test_a_reused_name_gets_none_of_the_old_team_s_races_or_logs(
        self, store: Store
    ) -> None:
        enter(store, "a")
        store.record_race(_report(store, ["a"], ["a"]), REPLAY, LOGS)
        old = _team(store, "a")
        store.delete("a")
        enter(store, "a", password="other")
        new = _team(store, "a")
        assert store.team_races(new.key) == []
        assert store.log(1, new.key) is None
        # The old sign-up is gone: it has no races left either.
        assert store.team_races(old.key) == []

    def test_a_version_the_team_no_longer_has_reads_as_its_bare_label(
        self, store: Store
    ) -> None:
        enter(store, "a")
        store.record_race(_report(store, ["a"], ["a"], versions={"a": 7}))
        [raced] = store.team_races(key(store, "a"))
        assert (raced.version.label, raced.version.description) == ("v7", "")


class TestVersionResults:
    def test_races_and_best_finish_by_version_void_races_aside(
        self, store: Store
    ) -> None:
        enter(store, "a")
        enter(store, "b")
        v2 = {"a": 2}
        store.record_race(_report(store, ["a", "b"], ["b", "a"]))
        store.record_race(_report(store, ["a", "b"], ["a", "b"]))
        store.record_race(
            replace(_report(store, ["a", "b"], []), void=True, void_reason="x")
        )
        store.record_race(_report(store, ["b", "a"], ["b"], versions=v2))
        assert store.version_results(key(store, "a")) == {
            1: VersionResults(races=2, best=1),
            2: VersionResults(races=1, best=None),
        }


class TestRaceHistory:
    def test_races_are_numbered_from_one_as_they_are_recorded(
        self, store: Store
    ) -> None:
        assert store.record_race(_report(store, ["a"], ["a"])).index == 1
        assert store.record_race(_report(store, ["a"], ["a"])).index == 2
        assert [r.index for r in store.races()] == [1, 2]

    def test_a_race_is_found_by_its_number(self, store: Store) -> None:
        store.record_race(_report(store, ["a"], ["a"]))
        found = store.race(1)
        assert found is not None and found.index == 1
        assert store.race(0) is None and store.race(2) is None

    def test_a_void_race_is_kept_but_does_not_score(self, store: Store) -> None:
        enter(store, "a")
        enter(store, "b")
        voided = _report(store, ["a", "b"], ["b", "a"])
        store.record_race(replace(voided, void=True, void_reason="stuck"))
        assert len(store.races()) == 1
        assert store.scoring_records() == []
        assert all(s.qualification_score == 0 for s in store.standings())

    def test_the_field_is_a_snapshot_not_a_live_view(self, store: Store) -> None:
        enter(store, "a")
        field = store.teams()
        store.push(key(store, "a"), WARNED)
        assert field[0].source == CAR


class TestStateFile:
    def test_a_new_store_from_the_same_dir_is_the_same_event(
        self, tmp_path: Path
    ) -> None:
        store = Store(state_dir=tmp_path)
        enter(store, "a")
        enter(store, "b")
        store.register("c", "pw")
        store.push(key(store, "b"), WARNED)
        store.record_race(_report(store, ["a", "b"], ["b", "a"]))
        voided = _report(store, ["b", "a"], ["b", "a"])
        store.record_race(replace(voided, void=True, void_reason="stuck"))
        store.lock_signups()

        restored = Store(state_dir=tmp_path)
        assert restored.accounts() == store.accounts()
        assert restored.races() == store.races()
        assert restored.standings() == store.standings()
        assert restored.signups_locked
        assert restored.restored_at is not None
        assert restored.authenticate("a", "pw") is not None
        assert restored.record_race(_report(store, ["a"], ["a"])).index == 3

    def test_a_lapless_qualification_report_survives_a_reload(
        self, tmp_path: Path
    ) -> None:
        store = Store(state_dir=tmp_path)
        store.record_race(replace(_report(store, ["a", "b"], ["b", "a"]), laps=None))
        store.record_race(_report(store, ["a"], ["a"]))
        restored = Store(state_dir=tmp_path)
        assert [r.laps for r in restored.races()] == [None, 1]
        assert restored.races() == store.races()

    def test_an_official_race_s_grid_points_survive_a_reload(
        self, tmp_path: Path
    ) -> None:
        store = Store(state_dir=tmp_path)
        points = {"a": 3, "b": 0}
        store.record_race(
            replace(_report(store, ["a", "b"], ["b", "a"]), qualification_points=points)
        )
        restored = Store(state_dir=tmp_path)
        assert restored.races()[0].qualification_points == points

    def test_a_race_s_passes_survive_a_reload(self, tmp_path: Path) -> None:
        store = Store(state_dir=tmp_path)
        report = _report(store, ["a", "b"], ["a", "b"])
        record = replace(report.record, passes=(1, -1))
        store.record_race(replace(report, record=record))
        restored = Store(state_dir=tmp_path)
        assert restored.races()[0].record.passes == (1, -1)

    def test_a_state_file_from_before_passes_loads_on_places_gained(
        self, tmp_path: Path
    ) -> None:
        store = Store(state_dir=tmp_path)
        enter(store, "a")
        enter(store, "b")
        store.record_race(_report(store, ["a", "b"], ["b", "a"]))
        path = tmp_path / STATE_FILE
        data = json.loads(path.read_text())
        for race in data["races"]:
            del race["passes"]
        path.write_text(json.dumps(data))
        restored = Store(state_dir=tmp_path)
        assert restored.races()[0].record.passes is None
        assert {s.team: s.qualification_score for s in restored.standings()} == {
            "a": 0,
            "b": 1,
        }

    def test_the_versions_a_race_ran_survive_a_reload(self, tmp_path: Path) -> None:
        store = Store(state_dir=tmp_path)
        cars = {"a": RacedCar(1.0, 3), "b": RacedCar(2.0, 1)}
        store.record_race(replace(_report(store, ["a", "b"], ["b", "a"]), cars=cars))
        restored = Store(state_dir=tmp_path)
        assert restored.races()[0].cars == cars

    def test_feedback_is_not_saved(self, tmp_path: Path) -> None:
        store = Store(state_dir=tmp_path)
        enter(store, "a")
        with pytest.raises(PushRejected):
            store.push(key(store, "a"), BROKEN)
        assert store.feedback(key(store, "a")) is not None
        restored = Store(state_dir=tmp_path)
        assert restored.feedback(key(restored, "a")) is None

    def test_a_replay_is_its_own_file_and_survives_a_reload(
        self, tmp_path: Path
    ) -> None:
        store = Store(state_dir=tmp_path)
        store.record_race(_report(store, ["a"], ["a"]), REPLAY)
        path = tmp_path / REPLAY_DIR / "1.json"
        assert store.replay(1) == path
        assert path.read_bytes() == REPLAY
        assert "frames" not in (tmp_path / STATE_FILE).read_text()
        restored = Store(state_dir=tmp_path)
        replay = restored.replay(1)
        assert isinstance(replay, Path) and replay.read_bytes() == REPLAY

    def test_a_void_race_keeps_no_replay(self, tmp_path: Path) -> None:
        store = Store(state_dir=tmp_path)
        voided = replace(_report(store, ["a"], ["a"]), void=True, void_reason="stuck")
        store.record_race(voided, REPLAY)
        assert store.replay(1) is None and store.replay(2) is None
        assert list((tmp_path / REPLAY_DIR).iterdir()) == []

    def test_a_replay_left_by_a_crash_before_the_race_was_saved_is_ignored(
        self, tmp_path: Path
    ) -> None:
        Store(state_dir=tmp_path)
        (tmp_path / REPLAY_DIR / "1.json").write_bytes(b"{}")
        store = Store(state_dir=tmp_path)
        assert store.replay(1) is None
        store.record_race(_report(store, ["a"], ["a"]), REPLAY)
        assert (tmp_path / REPLAY_DIR / "1.json").read_bytes() == REPLAY

    def test_a_replay_that_cannot_be_written_leaves_the_race_without_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store = Store(state_dir=tmp_path)
        (tmp_path / REPLAY_DIR).rmdir()
        report = store.record_race(_report(store, ["a"], ["a"]), REPLAY)
        assert store.replay(report.index) is None
        assert "cannot save" in capsys.readouterr().err
        assert Store(state_dir=tmp_path).races() == [report]

    def test_in_memory_a_replay_is_kept_as_bytes(self) -> None:
        store = Store()
        store.record_race(_report(store, ["a"], ["a"]), REPLAY)
        assert store.replay(1) == REPLAY

    def test_a_races_logs_are_their_own_file_and_survive_a_reload(
        self, tmp_path: Path
    ) -> None:
        store = Store(state_dir=tmp_path)
        enter(store, "a")
        store.record_race(_report(store, ["a", "b"], ["a", "b"]), REPLAY, LOGS)
        assert (tmp_path / LOG_DIR / "1.json").exists()
        assert "loaded" not in (tmp_path / STATE_FILE).read_text()
        assert store.log(1, key(store, "a")) == LOGS["a"]
        restored = Store(state_dir=tmp_path)
        assert restored.log(1, key(restored, "a")) == LOGS["a"]

    def test_a_team_that_printed_nothing_has_no_log(self) -> None:
        store = Store()
        enter(store, "a")
        enter(store, "b")
        store.record_race(_report(store, ["a", "b"], ["a", "b"]), REPLAY, LOGS)
        assert store.log(1, key(store, "b")) is None
        assert store.log(2, key(store, "a")) is None

    def test_a_void_race_keeps_no_logs(self, tmp_path: Path) -> None:
        store = Store(state_dir=tmp_path)
        enter(store, "a")
        voided = replace(_report(store, ["a"], ["a"]), void=True, void_reason="stuck")
        store.record_race(voided, REPLAY, LOGS)
        assert store.log(1, key(store, "a")) is None
        assert list((tmp_path / LOG_DIR).iterdir()) == []

    def test_every_version_survives_a_restart(self, tmp_path: Path) -> None:
        store = Store(state_dir=tmp_path)
        store.register("a", "pw")
        store.push(key(store, "a"), CAR, "first")
        store.push(key(store, "a"), WARNED, "second")
        assert CAR.strip() not in (tmp_path / STATE_FILE).read_text()
        restored = Store(state_dir=tmp_path)
        assert restored.accounts() == store.accounts()
        (team,) = restored.teams()
        assert team.source == WARNED
        assert [v.description for v in team.versions] == ["first", "second"]
        assert restored.version_source(key(restored, "a"), 1) == CAR
        assert restored.version_source(key(restored, "a"), 2) == WARNED

    def test_a_version_s_source_is_a_file_under_the_team_s_key(
        self, tmp_path: Path
    ) -> None:
        store = Store(state_dir=tmp_path)
        enter(store, "a")
        (team,) = store.teams()
        directory = tmp_path / CONTROLLER_DIR / repr(team.registered_at)
        assert (directory / "1.py").read_text(encoding="utf-8") == CAR
        store.delete("a")
        assert not directory.exists()
        assert Store(state_dir=tmp_path).accounts() == []

    def test_a_source_that_cannot_be_written_refuses_the_push(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        store = Store(state_dir=tmp_path)
        enter(store, "a")
        monkeypatch.setattr(store_module.os, "fsync", _raise_oserror)
        with pytest.raises(PushRejected) as exc:
            store.push(key(store, "a"), WARNED, "lost?")
        monkeypatch.undo()
        assert exc.value.status_code == 500
        feedback = store.feedback(key(store, "a"))
        assert feedback is not None and feedback.draft == WARNED
        assert store.teams()[0].pushes == 1
        assert "cannot save" in capsys.readouterr().err
        assert store.save_error is not None
        assert Store(state_dir=tmp_path).teams()[0].pushes == 1

    def test_a_missing_latest_source_refuses_to_load(self, tmp_path: Path) -> None:
        store = Store(state_dir=tmp_path)
        enter(store, "a")
        (team,) = store.teams()
        (tmp_path / CONTROLLER_DIR / repr(team.registered_at) / "1.py").unlink()
        with pytest.raises(StateError, match=STATE_FILE):
            Store(state_dir=tmp_path)

    def test_a_refused_push_does_not_rewrite_the_file(self, tmp_path: Path) -> None:
        store = Store(state_dir=tmp_path)
        enter(store, "a")
        before = (tmp_path / STATE_FILE).stat().st_mtime_ns
        saved_at = store.saved_at
        with pytest.raises(PushRejected):
            store.push(key(store, "a"), BROKEN)
        assert (tmp_path / STATE_FILE).stat().st_mtime_ns == before
        assert store.saved_at == saved_at

    def test_a_deletion_is_saved(self, tmp_path: Path) -> None:
        store = Store(state_dir=tmp_path)
        enter(store, "a")
        store.delete("a")
        assert Store(state_dir=tmp_path).accounts() == []

    def test_a_crash_mid_write_leaves_the_previous_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = Store(state_dir=tmp_path)
        enter(store, "a")
        before = (tmp_path / STATE_FILE).read_bytes()

        def crash(*args: object) -> None:
            raise OSError("disk pulled")

        monkeypatch.setattr(store_module.os, "replace", crash)
        store.register("b", "pw")
        monkeypatch.undo()

        assert (tmp_path / STATE_FILE).read_bytes() == before
        assert sorted(os.listdir(tmp_path)) == [
            CONTROLLER_DIR,
            LOG_DIR,
            REPLAY_DIR,
            STATE_FILE,
        ]
        assert os.listdir(tmp_path / REPLAY_DIR) == []
        assert store.save_error is not None and "disk pulled" in store.save_error
        assert [t.name for t in store.accounts()] == ["a", "b"]
        assert [t.name for t in Store(state_dir=tmp_path).accounts()] == ["a"]

    def test_the_next_good_save_clears_the_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = Store(state_dir=tmp_path)
        monkeypatch.setattr(store_module.os, "fsync", _raise_oserror)
        store.register("a", "pw")
        assert store.save_error is not None
        monkeypatch.undo()
        store.register("b", "pw")
        assert store.save_error is None
        assert len(Store(state_dir=tmp_path).accounts()) == 2

    @pytest.mark.parametrize(
        "content",
        [
            "{not json",
            "[]",
            json.dumps({"version": 3, "teams": [], "races": []}),
            json.dumps({"teams": [], "races": []}),
            json.dumps(
                {"version": 1, "teams": [], "races": [], "signups_locked": False}
            ),
            json.dumps({"version": 2, "teams": [], "races": [], "signups_locked": 1}),
            json.dumps({"version": 2, "teams": [{"name": "a"}], "races": []}),
        ],
    )
    def test_a_file_it_cannot_read_refuses_to_load_and_is_kept(
        self, tmp_path: Path, content: str
    ) -> None:
        (tmp_path / STATE_FILE).write_text(content)
        with pytest.raises(StateError, match=STATE_FILE):
            Store(state_dir=tmp_path)
        assert (tmp_path / STATE_FILE).read_text() == content

    def test_the_server_refuses_to_start_over_a_bad_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / STATE_FILE).write_text(json.dumps({"version": 99}))
        assert main(["--state-dir", str(tmp_path)]) == 1
        assert str(tmp_path / STATE_FILE) in capsys.readouterr().err

    def test_without_a_state_dir_nothing_is_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(store_module.tempfile, "mkstemp", _raise_oserror)
        store = Store()
        enter(store, "a")
        store.record_race(_report(store, ["a"], ["a"]), REPLAY)
        store.lock_signups()
        assert store.state_file is None and store.saved_at is None
        assert list(tmp_path.iterdir()) == []


def _raise_oserror(*args: object) -> None:
    raise OSError("no space left")
