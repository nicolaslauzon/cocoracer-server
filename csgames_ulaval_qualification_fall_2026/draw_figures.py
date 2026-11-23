"""Rebuild the leaderboards and figures of `csgames_ulaval_qualification_fall_2026/`.

Run from the repo root, with the server project so the scoring code is the
server's own:

    uv run --project server --with matplotlib \
        csgames_ulaval_qualification_fall_2026/draw_figures.py ~/csgames-state-backup/state.json

The input is the server's `state.json`. It holds password hashes, so it
stays out of the repo; only the CSVs and figures this script derives from
it are committed. Races after `EVENT_END` ran on a restored copy of the
state, not during the event, and are left out.
"""

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from cocoracer_server.scoring import (
    RaceKind,
    RaceRecord,
    by_qualification_score,
    final_standings,
    qualification_scores,
)
from cocoracer_server.store import RaceReport

OUT = Path(__file__).resolve().parent
IMG = OUT / "img"

# The last race of the event was cut by a server restart (race 85, void).
# Everything dated after it was raced on a restored state.
EVENT_END = datetime(2026, 10, 9).timestamp()

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
MUTED = "#52514e"
FAINT = "#c3c2b7"
ACCENT = "#2a78d6"
PODIUM = ["#2a78d6", "#eb6834", "#1baf7a"]  # first, second, third

plt.rcParams.update(
    {
        "font.size": 10,
        "font.family": "DejaVu Sans",
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "text.color": INK,
        "axes.edgecolor": FAINT,
        "axes.labelcolor": MUTED,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
    }
)


def load_event(state: Path) -> list[RaceReport]:
    """The event's races in order, void ones left out as the server does."""
    data = json.loads(state.read_text())
    reports = [RaceReport.from_json(r) for r in data["races"]]
    return [r for r in reports if r.finished_at < EVENT_END and not r.void]


def qualification_progression(
    records: list[RaceRecord], teams: list[str]
) -> dict[str, list[int]]:
    """Each team's qualification score after each qualification race: the
    sum of its last `QUALIFICATION_WINDOW` races, as the live board showed."""
    qualifications = [r for r in records if r.kind is RaceKind.QUALIFICATION]
    lines: dict[str, list[int]] = {team: [] for team in teams}
    for n in range(1, len(qualifications) + 1):
        scores = qualification_scores(qualifications[:n])
        for team in teams:
            lines[team].append(scores.get(team, 0))
    return lines


def write_csvs(
    records: list[RaceRecord], teams: list[str], lines: dict[str, list[int]]
) -> None:
    standings = final_standings(records, teams)
    with (OUT / "leaderboard_official.csv").open("w", newline="") as f:
        out = csv.writer(f)
        out.writerow(["rank", "team", "results", "points"])
        for rank, s in enumerate(standings, start=1):
            out.writerow(
                [
                    rank,
                    s.team,
                    ", ".join(map(str, s.official_positions)),
                    s.official_score,
                ]
            )
    with (OUT / "leaderboard_qualification.csv").open("w", newline="") as f:
        out = csv.writer(f)
        out.writerow(["rank", "team", "points"])
        for rank, s in enumerate(by_qualification_score(standings), start=1):
            out.writerow([rank, s.team, s.qualification_score])
    with (OUT / "qualification_progression.csv").open("w", newline="") as f:
        out = csv.writer(f)
        out.writerow(["race", *teams])
        for i in range(len(next(iter(lines.values())))):
            out.writerow([i + 1, *(lines[t][i] for t in teams)])


def qualification_figure(lines: dict[str, list[int]], order: list[str]) -> None:
    """One panel per team, its score line over the others' in grey."""
    n = len(next(iter(lines.values())))
    top = max(max(v) for v in lines.values())
    fig, axes = plt.subplots(
        2, 5, figsize=(13, 5.6), sharex=True, sharey=True, constrained_layout=True
    )
    xs = list(range(1, n + 1))
    for ax, (rank, team) in zip(axes.flat, enumerate(order, start=1), strict=True):
        for other in order:
            if other != team:
                ax.plot(xs, lines[other], color=FAINT, lw=0.8, alpha=0.7)
        ax.fill_between(xs, lines[team], color=ACCENT, alpha=0.12, lw=0)
        ax.plot(xs, lines[team], color=ACCENT, lw=2)
        ax.plot(
            xs[-1],
            lines[team][-1],
            "o",
            color=ACCENT,
            ms=6,
            mec=SURFACE,
            mew=2,
            clip_on=False,
            zorder=5,
        )
        ax.set_title(
            f"{rank}. {team}  {lines[team][-1]}",
            loc="left",
            fontsize=10,
            fontweight="bold",
        )
        ax.set_xlim(1, n)
        ax.set_ylim(0, top * 1.08)
        ax.set_xticks([1, 20, 40, 60, n])
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", color=FAINT, lw=0.5, alpha=0.5)
        ax.set_axisbelow(True)
    for ax in axes[:, 0]:
        ax.set_ylabel("points, last 8 races")
    for ax in axes[1, :]:
        ax.set_xlabel("qualification race")
    fig.suptitle(
        "Qualification score after each race, teams ranked by their final score",
        x=0.01,
        ha="left",
        fontsize=13,
        fontweight="bold",
    )
    fig.savefig(IMG / "qualification_progression.png", dpi=150)
    plt.close(fig)


def spread(values: list[float], gap: float) -> list[float]:
    """Push sorted label positions apart so neighbors sit `gap` apart."""
    out = list(values)
    for i in range(1, len(out)):
        out[i] = max(out[i], out[i - 1] + gap)
    shift = (out[-1] - values[-1]) / 2
    return [v - shift for v in out]


def official_figure(records: list[RaceRecord], teams: list[str]) -> None:
    """Cumulative official score after each official race; lower wins."""
    standings = final_standings(records, teams)
    races = sum(1 for r in records if r.kind is RaceKind.OFFICIAL)
    xs = list(range(races + 1))
    fig, ax = plt.subplots(figsize=(10, 5.6), constrained_layout=True)
    ends = []
    for rank, s in enumerate(standings, start=1):
        total = 0
        ys = [0]
        for p in s.official_positions:
            total += p or 0
            ys.append(total)
        podium = rank <= len(PODIUM)
        color = PODIUM[rank - 1] if podium else FAINT
        ax.plot(
            xs, ys, color=color, lw=2.4 if podium else 1.4, zorder=3 if podium else 2
        )
        ax.plot(xs[1:], ys[1:], "o", color=color, ms=5, mec=SURFACE, mew=1.5, zorder=4)
        ends.append((ys[-1], f"{s.team}  {ys[-1]}", color if podium else MUTED, podium))
    labels = spread([e[0] for e in ends], gap=1.9)
    for (y, text, color, podium), ly in zip(ends, labels, strict=True):
        ax.annotate(
            text,
            (races, y),
            xytext=(races + 0.12, ly),
            textcoords="data",
            va="center",
            color=INK if podium else MUTED,
            fontweight="bold" if podium else "normal",
            fontsize=9.5,
        )
    ax.set_xlim(0, races + 1.9)
    ax.set_ylim(0, None)
    ax.set_xticks(xs, ["start", *(f"race {i}" for i in xs[1:])])
    ax.set_ylabel("sum of finishing positions, lower wins")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color=FAINT, lw=0.5, alpha=0.5)
    ax.set_axisbelow(True)
    ax.set_title(
        "Official score after each of the five official races",
        loc="left",
        fontsize=13,
        fontweight="bold",
    )
    fig.savefig(IMG / "official_progression.png", dpi=150)
    plt.close(fig)


def main() -> None:
    reports = load_event(Path(sys.argv[1]).expanduser())
    records = [r.record for r in reports]
    teams = sorted({t for r in records for t in r.grid})
    lines = qualification_progression(records, teams)
    final = by_qualification_score(final_standings(records, teams))
    order = [s.team for s in final]
    OUT.mkdir(exist_ok=True)
    IMG.mkdir(exist_ok=True)
    write_csvs(records, teams, lines)
    qualification_figure(lines, order)
    official_figure(records, teams)


if __name__ == "__main__":
    main()
