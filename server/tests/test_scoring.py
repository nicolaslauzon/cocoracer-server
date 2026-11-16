"""The event's two scoring systems and the standings they produce."""

import pytest

from cocoracer_server.scoring import (
    QUALIFICATION_WINDOW,
    RaceKind,
    RaceRecord,
    by_qualification_score,
    final_standings,
    passes,
    positions_gained,
    qualification_scores,
    race_gain,
)

# A lap of the made-up track the pass tests race on.
LAP = 100.0


def _qualifying(grid: list[str], classified: list[str]) -> RaceRecord:
    return RaceRecord(RaceKind.QUALIFICATION, tuple(grid), tuple(classified))


def _official(grid: list[str], classified: list[str]) -> RaceRecord:
    return RaceRecord(RaceKind.OFFICIAL, tuple(grid), tuple(classified))


class TestPositionsGained:
    def test_a_car_that_climbs_scores_one_point_per_place(self) -> None:
        gained = positions_gained(("a", "b", "c", "d"), ("d", "a", "b", "c"))
        assert gained["d"] == 3

    def test_losing_places_costs_nothing(self) -> None:
        gained = positions_gained(("a", "b", "c", "d"), ("d", "a", "b", "c"))
        assert gained["a"] == 0
        assert gained["b"] == 0
        assert gained["c"] == 0

    def test_an_unchanged_order_scores_nothing(self) -> None:
        gained = positions_gained(("a", "b", "c"), ("a", "b", "c"))
        assert set(gained.values()) == {0}

    def test_a_full_reversal_scores_the_whole_field(self) -> None:
        gained = positions_gained(("a", "b", "c", "d"), ("d", "c", "b", "a"))
        assert gained == {"a": 0, "b": 0, "c": 1, "d": 3}

    def test_a_single_car_race_scores_nothing(self) -> None:
        assert positions_gained(("a",), ("a",)) == {"a": 0}

    def test_a_grid_and_a_classification_must_hold_the_same_teams(self) -> None:
        with pytest.raises(ValueError, match="same teams"):
            positions_gained(("a", "b"), ("a", "c"))


class TestPasses:
    def test_overtaking_a_car_is_one_pass_and_being_overtaken_takes_one(
        self,
    ) -> None:
        distances = {"a": (0.0, 50.0), "b": (-4.0, 60.0)}
        assert passes(("a", "b"), distances, LAP) == (-1, 1)

    def test_lapping_a_car_is_a_pass(self) -> None:
        # a, on pole, stays ahead and laps b.
        distances = {"a": (0.0, 250.0), "b": (-4.0, 120.0)}
        assert passes(("a", "b"), distances, LAP) == (1, -1)

    def test_lapping_a_car_twice_is_two_passes(self) -> None:
        distances = {"a": (0.0, 250.0), "b": (-4.0, 30.0)}
        assert passes(("a", "b"), distances, LAP) == (2, -2)

    def test_overtaking_then_lapping_a_car_is_two_passes(self) -> None:
        distances = {"a": (-4.0, 250.0), "b": (0.0, 120.0)}
        assert passes(("a", "b"), distances, LAP) == (2, -2)

    def test_a_pass_taken_back_counts_nothing(self) -> None:
        # Whatever happened in between, b is still just ahead.
        distances = {"a": (-4.0, 140.0), "b": (0.0, 141.0)}
        assert passes(("a", "b"), distances, LAP) == (0, 0)

    def test_a_car_that_never_moved_is_lapped_by_the_field(self) -> None:
        distances = {"a": (0.0, 180.0), "b": (-4.0, 170.0), "dead": (-8.0, -8.0)}
        assert passes(("a", "b", "dead"), distances, LAP) == (1, 1, -2)

    def test_without_lapping_it_is_the_positions_gained(self) -> None:
        grid = ("a", "b", "c", "d")
        distances = {
            "a": (0.0, 60.0),
            "b": (-4.0, 90.0),
            "c": (-8.0, 30.0),
            "d": (-12.0, 75.0),
        }
        classified = ("b", "d", "a", "c")
        start = {team: i for i, team in enumerate(grid)}
        signed = tuple(start[t] - classified.index(t) for t in grid)
        assert passes(grid, distances, LAP) == signed


class TestRaceGain:
    def test_a_qualification_pays_its_net_passes(self) -> None:
        record = RaceRecord(
            RaceKind.QUALIFICATION, ("a", "b"), ("a", "b"), passes=(1, -1)
        )
        assert race_gain(record, slot=1, finish=1) == 1
        assert race_gain(record, slot=2, finish=2) == -1

    def test_an_official_race_pays_its_places_gained(self) -> None:
        record = RaceRecord(RaceKind.OFFICIAL, ("a", "b"), ("b", "a"), passes=(3, 0))
        assert race_gain(record, slot=2, finish=1) == 1

    def test_a_qualification_without_passes_pays_its_places_gained(self) -> None:
        record = _qualifying(["a", "b"], ["b", "a"])
        assert race_gain(record, slot=2, finish=1) == 1


class TestQualificationScores:
    def test_a_lap_counts_as_a_pass(self) -> None:
        record = RaceRecord(
            RaceKind.QUALIFICATION, ("a", "b"), ("a", "b"), passes=(1, -1)
        )
        assert qualification_scores([record]) == {"a": 1, "b": 0}

    def test_only_the_last_qualifications_count(self) -> None:
        old = _qualifying(["a", "b"], ["b", "a"])  # b +1, out of the window
        recent = [_qualifying(["b", "a"], ["a", "b"])] * QUALIFICATION_WINDOW
        assert qualification_scores([old, *recent]) == {
            "a": QUALIFICATION_WINDOW,
            "b": 0,
        }

    def test_official_races_take_no_place_in_the_window(self) -> None:
        records = [
            _qualifying(["a", "b"], ["b", "a"]),
            *[_official(["a", "b"], ["a", "b"])] * QUALIFICATION_WINDOW,
        ]
        assert qualification_scores(records) == {"a": 0, "b": 1}

    def test_points_accumulate_across_races(self) -> None:
        records = [
            _qualifying(["a", "b", "c"], ["c", "a", "b"]),  # c +2
            _qualifying(["c", "a", "b"], ["b", "c", "a"]),  # b +2, c +0, a +0
        ]
        assert qualification_scores(records) == {"a": 0, "b": 2, "c": 2}

    def test_official_races_do_not_feed_the_qualification_score(self) -> None:
        records = [
            _qualifying(["a", "b"], ["a", "b"]),
            _official(["a", "b"], ["b", "a"]),
        ]
        assert qualification_scores(records) == {"a": 0, "b": 0}

    def test_a_team_that_joined_late_is_scored_on_the_races_it_ran(self) -> None:
        records = [
            _qualifying(["a", "b"], ["b", "a"]),
            _qualifying(["a", "b", "late"], ["late", "a", "b"]),
        ]
        scores = qualification_scores(records)
        assert scores["late"] == 2
        assert scores["b"] == 1

    def test_no_races_is_an_empty_table(self) -> None:
        assert qualification_scores([]) == {}


class TestFinalStandings:
    def test_the_lowest_official_score_wins(self) -> None:
        records = [
            _qualifying(["a", "b"], ["a", "b"]),
            _official(["a", "b"], ["b", "a"]),
        ]
        standings = final_standings(records, ["a", "b"])
        assert [s.team for s in standings] == ["b", "a"]
        assert standings[0].official_score == 1

    def test_the_official_score_is_the_sum_of_the_finishing_positions(self) -> None:
        records = [
            _official(["a", "b"], ["a", "b"]),
            _official(["a", "b"], ["b", "a"]),
            _official(["a", "b"], ["a", "b"]),
        ]
        standings = {s.team: s for s in final_standings(records, ["a", "b"])}
        assert standings["a"].official_score == 4
        assert standings["b"].official_score == 5
        assert standings["a"].official_positions == (1, 2, 1)
        assert standings["b"].official_positions == (2, 1, 2)

    def test_a_missed_official_race_counts_as_its_field_size_plus_one(
        self,
    ) -> None:
        """A team let in after the freeze was lifted missed the races before."""
        records = [
            _official(["a", "b"], ["b", "a"]),
            _official(["a", "b", "late"], ["late", "a", "b"]),
        ]
        standings = {s.team: s for s in final_standings(records, ["a", "b", "late"])}
        assert standings["late"].official_positions == (None, 1)
        assert standings["late"].official_score == 3 + 1
        assert standings["late"].best_official == 1
        assert standings["a"].official_score == 2 + 2

    def test_a_car_outside_the_field_still_shapes_the_others(self) -> None:
        """How the store scores a deleted team's car (`Store.scoring_records`):
        under a key no team has, it scores for nobody, but the places it
        took and the field it made still count for everyone else."""
        records = [
            _qualifying(["a", "#1 c", "b"], ["#1 c", "b", "a"]),
            _official(["a", "#2 c", "c"], ["#2 c", "a", "c"]),
        ]
        standings = {s.team: s for s in final_standings(records, ["a", "b", "c"])}
        assert set(standings) == {"a", "b", "c"}
        assert standings["b"].qualification_score == 1
        assert standings["c"].qualification_score == 0
        assert standings["a"].official_positions == (2,)
        assert standings["c"].official_positions == (3,)

    def test_a_team_missing_a_later_race_is_penalised_in_it(self) -> None:
        records = [
            _official(["a", "b", "c"], ["c", "b", "a"]),
            _official(["a", "b"], ["a", "b"]),
        ]
        standings = {s.team: s for s in final_standings(records, ["a", "b", "c"])}
        assert standings["c"].official_positions == (1, None)
        assert standings["c"].official_score == 1 + 3

    def test_a_tie_on_official_score_breaks_on_qualification_score(self) -> None:
        records = [
            _qualifying(["a", "b"], ["b", "a"]),  # b gained 1, a gained 0
            _official(["a", "b"], ["a", "b"]),
            _official(["a", "b"], ["b", "a"]),
        ]
        standings = final_standings(records, ["a", "b"])
        assert [s.team for s in standings] == ["b", "a"]
        assert standings[0].official_score == standings[1].official_score

    def test_a_tie_on_both_breaks_on_the_best_single_official_finish(self) -> None:
        """Same sum and same qualification points; the car that once won
        an official race is placed ahead of one that never did."""
        field = ["a", "b", "c", "d"]
        records = [
            _official(field, ["a", "d", "b", "c"]),  # a1 d2 b3 c4
            _official(field, ["c", "b", "d", "a"]),  # c1 b2 d3 a4
        ]
        standings = {s.team: s for s in final_standings(records, field)}
        assert standings["a"].official_score == standings["b"].official_score == 5
        assert standings["a"].best_official == 1
        assert standings["b"].best_official == 2
        order = [s.team for s in final_standings(records, field)]
        assert order.index("a") < order.index("b")

    def test_teams_with_no_official_races_rank_after_every_scored_team(self) -> None:
        records = [
            _qualifying(["a", "b", "c"], ["c", "b", "a"]),
            _official(["a", "b"], ["b", "a"]),
        ]
        standings = final_standings(records, ["a", "b", "c"])
        assert [s.team for s in standings] == ["b", "a", "c"]
        assert standings[-1].official_score is None

    def test_unscored_teams_are_ordered_by_qualification_score(self) -> None:
        # d gains 3, c gains 1, a and b gain nothing and fall back to name.
        records = [_qualifying(["a", "b", "c", "d"], ["d", "c", "a", "b"])]
        standings = final_standings(records, ["a", "b", "c", "d"])
        assert [s.team for s in standings] == ["d", "c", "a", "b"]
        assert [s.qualification_score for s in standings] == [3, 1, 0, 0]
        assert all(s.official_score is None for s in standings)

    def test_standings_carry_both_scores(self) -> None:
        records = [
            _qualifying(["a", "b"], ["b", "a"]),
            _official(["a", "b"], ["b", "a"]),
        ]
        top = final_standings(records, ["a", "b"])[0]
        assert top.team == "b"
        assert top.qualification_score == 1
        assert top.official_score == 1
        assert top.best_official == 1

    def test_a_wholly_tied_pair_is_ordered_by_name_for_determinism(self) -> None:
        records = [_official(["a", "b"], ["a", "b"]), _official(["a", "b"], ["b", "a"])]
        assert [s.team for s in final_standings(records, ["a", "b"])] == ["a", "b"]

    def test_a_team_that_has_left_the_field_is_dropped(self) -> None:
        records = [_qualifying(["a", "b", "c"], ["c", "b", "a"])]
        standings = final_standings(records, ["a", "b"])
        assert [s.team for s in standings] == ["a", "b"]

    def test_a_team_that_has_not_raced_yet_is_carried_on_zero(self) -> None:
        records = [_qualifying(["a", "b"], ["b", "a"])]
        standings = {s.team: s for s in final_standings(records, ["a", "b", "late"])}
        assert standings["late"].qualification_score == 0
        assert standings["late"].official_score is None

    def test_no_races_is_no_standings(self) -> None:
        assert final_standings([], []) == []


class TestQualificationOrdering:
    def test_the_most_positions_gained_leads(self) -> None:
        # c gains 2, b holds its place, a loses two and is floored at zero.
        records = [_qualifying(["a", "b", "c"], ["c", "b", "a"])]
        standings = by_qualification_score(final_standings(records, ["a", "b", "c"]))
        assert [s.team for s in standings] == ["c", "a", "b"]
        assert [s.qualification_score for s in standings] == [2, 0, 0]

    def test_it_ignores_the_official_score(self) -> None:
        """The two boards answer different questions: the official winner is
        not promoted on the qualification board."""
        records = [
            _qualifying(["a", "b"], ["b", "a"]),  # b gains 1
            _official(["a", "b"], ["a", "b"]),  # a wins the official race
        ]
        standings = by_qualification_score(final_standings(records, ["a", "b"]))
        assert [s.team for s in standings] == ["b", "a"]

    def test_a_tie_is_broken_by_name(self) -> None:
        records = [_qualifying(["a", "b"], ["a", "b"])]
        standings = by_qualification_score(final_standings(records, ["b", "a"]))
        assert [s.team for s in standings] == ["a", "b"]
