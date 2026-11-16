"""The event's two scoring systems.

Pure functions over finished races: no I/O, no framework, no clock. A race
arrives already classified by the engine — finishers in finish order, then
non-finishers ranked by how far they got — so scoring only ever works with
positions.

**Qualification score**: one point per pass, lapping a car included, a
pass taken back cancelling it and the race's total never negative, summed
over the event's last `QUALIFICATION_WINDOW` qualification races. Because
the qualification grid is shuffled at random rather than ordered by score,
this measures overtaking without the feedback loop that would otherwise
flatten the table; see `docs/adr/0001-two-tier-scoring.md`.

**Official score**: the sum of the finishing positions over the official
races. Lowest wins the challenge. A team that missed an official race (it
joined the field after it) counts as last in it: that race's field
size plus one.
"""

import enum
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

# How many of the event's latest qualification races the qualification
# score sums.
QUALIFICATION_WINDOW = 8


class RaceKind(enum.Enum):
    QUALIFICATION = "qualification"
    OFFICIAL = "official"


@dataclass(frozen=True)
class RaceRecord:
    """One finished race: who started where, and who ended up where.

    `classified` is the full field in finishing order, P1 first, with
    non-finishers ranked last by progression. `passes` is each car's net
    passes (`passes`), unfloored and in grid order; it is None for a race
    recorded before passes were counted, which scores positions gained.
    """

    kind: RaceKind
    grid: tuple[str, ...]
    classified: tuple[str, ...]
    passes: tuple[int, ...] | None = None


@dataclass(frozen=True)
class Standing:
    """One team's line in the final standings.

    `official_positions` holds the team's finishing position in each official
    race, in race order, with None for a race it was not in; it is empty for a
    team that has not run one. `official_score` is their sum, a missed race
    counting as its field size plus one, and None when the team has run no
    official race.
    """

    team: str
    official_score: int | None
    qualification_score: int
    best_official: int | None
    official_positions: tuple[int | None, ...] = ()


def positions_gained(grid: Sequence[str], classified: Sequence[str]) -> dict[str, int]:
    """Points for one race: places gained, floored at zero.

    Losing places costs nothing, so a crash is cheap and an overtake is the
    only thing that pays.
    """
    if set(grid) != set(classified) or len(grid) != len(classified):
        raise ValueError("grid and classification must hold the same teams")
    start = {team: i for i, team in enumerate(grid)}
    return {team: max(0, start[team] - i) for i, team in enumerate(classified)}


def passes(
    grid: Sequence[str],
    distances: Mapping[str, tuple[float, float]],
    lap_length: float,
) -> tuple[int, ...]:
    """Each car's net passes in one race, in grid order, unfloored.

    `distances` is each car's distance along the track at the release and
    at the end, continuous across laps. Car a passes car b each time a's
    lead over b climbs through a multiple of the lap: through zero is an
    overtake, through one lap is lapping b. Dropping back through one takes
    a pass back, so two cars swapping places side by side earn nothing.
    Without lapping, this is the car's positions gained, unfloored.
    """

    def laps_ahead(a: str, b: str, at: int) -> int:
        return math.floor((distances[a][at] - distances[b][at]) / lap_length)

    return tuple(
        sum(laps_ahead(a, b, 1) - laps_ahead(a, b, 0) for b in grid if b != a)
        for a in grid
    )


def qualification_points(record: RaceRecord) -> dict[str, int]:
    """Points for one qualification: net passes floored at zero, or
    positions gained for a race recorded before passes were counted."""
    if record.passes is None:
        return positions_gained(record.grid, record.classified)
    pairs = zip(record.grid, record.passes, strict=True)
    return {team: max(0, n) for team, n in pairs}


def race_gain(record: RaceRecord, slot: int, finish: int) -> int:
    """What one race paid the car on grid `slot` that finished `finish`
    (both from 1), signed and unfloored: its net passes in a qualification
    that counted them, its places gained otherwise. The tables show this;
    the qualification score floors it."""
    if record.kind is RaceKind.QUALIFICATION and record.passes is not None:
        return record.passes[slot - 1]
    return slot - finish


def qualification_scores(records: Iterable[RaceRecord]) -> dict[str, int]:
    """Points summed over the last `QUALIFICATION_WINDOW` qualification
    races; a team not in one of them scores nothing from it."""
    qualifications = [r for r in records if r.kind is RaceKind.QUALIFICATION]
    scores: dict[str, int] = {}
    for record in qualifications[-QUALIFICATION_WINDOW:]:
        for team, points in qualification_points(record).items():
            scores[team] = scores.get(team, 0) + points
    return scores


def _official_results(
    records: Iterable[RaceRecord],
) -> tuple[list[int], dict[str, list[int | None]]]:
    """Each official race's field size, and each team's positions in them.

    A team's list has one entry per official race, None for one it missed;
    a team in no official race has no list.
    """
    sizes: list[int] = []
    positions: dict[str, list[int | None]] = {}
    for record in records:
        if record.kind is not RaceKind.OFFICIAL:
            continue
        race = len(sizes)
        sizes.append(len(record.classified))
        for index, team in enumerate(record.classified, start=1):
            positions.setdefault(team, [None] * race).append(index)
        for line in positions.values():
            if len(line) == race:
                line.append(None)
    return sizes, positions


def _official_score(positions: Sequence[int | None], sizes: Sequence[int]) -> int:
    """The sum of the positions; a missed race counts as last-plus-one in it."""
    pairs = zip(positions, sizes, strict=True)
    return sum(size + 1 if p is None else p for p, size in pairs)


def _rows(records: Sequence[RaceRecord], field: Iterable[str]) -> list[Standing]:
    """One line per team in the field, in no particular order.

    A team that raced and has since been deleted is dropped — it is not in
    the field any more — while a team that has registered but not yet raced
    is carried on zero. Its presence in a past race still shaped the
    positions the other cars gained, and the size of the official fields,
    so the records themselves are untouched.
    """
    qualification = qualification_scores(records)
    sizes, positions = _official_results(records)
    rows = []
    for team in field:
        mine = positions.get(team)
        raced = [p for p in mine or () if p is not None]
        rows.append(
            Standing(
                team=team,
                official_score=None if mine is None else _official_score(mine, sizes),
                qualification_score=qualification.get(team, 0),
                best_official=min(raced) if raced else None,
                official_positions=tuple(mine or ()),
            )
        )
    return rows


def final_standings(
    records: Sequence[RaceRecord], field: Iterable[str]
) -> list[Standing]:
    """The field ranked for the official board, the winner first.

    Ordered by official score ascending; ties break on qualification score
    descending, then on the best single official finish, then on name so the
    order never depends on dictionary insertion. A team with no official
    race has no official score and is ranked after every team that has one.
    """
    standings = _rows(records, field)
    standings.sort(
        key=lambda s: (
            s.official_score is None,
            s.official_score if s.official_score is not None else 0,
            -s.qualification_score,
            s.best_official if s.best_official is not None else 0,
            s.team,
        )
    )
    return standings


def by_qualification_score(standings: Iterable[Standing]) -> list[Standing]:
    """The same lines ranked for the qualification board.

    Most qualification points first, ties on name. This is the *only* other
    ordering of the standings: nothing outside this module sorts them.
    """
    return sorted(standings, key=lambda s: (-s.qualification_score, s.team))
