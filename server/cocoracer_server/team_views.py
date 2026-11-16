"""The tabs about a team's code and its races, as strings.

`push_page` is the push tab: a description for the next version, a Push
button that picks a controller and pushes it, a Pull button that downloads
it back, what became of the last push, and the team's versions. `replays_page` is the replays tab:
a team's own race history, or every race for the admin, each row linked to `/replays/{index}`; `race_page` is that one race's replay, and
`missing_race_page` the answer for a race that was never run.

Like `views`, whose frame and header they share, these are pure functions
of the store: no framework, no request, no I/O, French on the page and
English in the code.
"""

import html
import json
import time

from cocoracer_server.scheduler import Scheduler
from cocoracer_server.scoring import RaceKind, race_gain
from cocoracer_server.store import (
    MAX_DESCRIPTION_CHARS,
    PushFeedback,
    RaceReport,
    Store,
    Team,
    TeamRace,
    VersionResults,
)
from cocoracer_server.views import Viewer, empty_row, tab_page

_KINDS = {
    RaceKind.QUALIFICATION: "Qualification",
    RaceKind.OFFICIAL: "Officielle",
}

# A car's status at the end of a race, as the history and the race page say
# it. The keys are cocoracer's `VehicleStatus` values. "terminé" covers both
# an official finisher and a car still out when the race ended; "DNF",
# "pause" and "ghost" stay in racing English (AGENTS.md, Language).
# static/replay.js's `STATUS_LABELS` holds the same strings.
_STATUSES = {
    "racing": "en course",
    "paused": "pause",
    "ghost": "ghost",
    "finished": "terminé",
    "done": "terminé",
    "dnf": "DNF",
}


# The headers of the facts that more than one table shows, so the same fact
# reads the same everywhere (the boards in `views` use "#" and "Équipe" too).
RANK = "#"
TEAM = "Équipe"
START = "Position de départ"
POSITION = "Position d'arrivée"
GAIN = "Gains"
STATUS = "Statut"
QUALIFICATION_POINTS = "Points de qualification"
FIRST = "Premier"
VERSION = "Version"
RESULTS = "Résultats"
DESCRIPTION = "Description"


def push_page(
    store: Store, scheduler: Scheduler, team: Team, refused: str | None = None
) -> str:
    """The push tab: an optional description of the next version, a Push
    button that picks a controller and pushes it as that version, a Pull
    button that downloads the controller the team is racing, the last
    push's result, and the team's versions (`_versions`).

    Picking a file submits the form; without JavaScript a `<noscript>`
    button does. So the description has to be typed first, and sits left
    of Push to say so; a refused push hands it back. Pull is shown disabled
    until the team has a controller to pull. `refused` is why the last
    description saved from the versions table was refused, if it was.
    """
    feedback = store.feedback(team.key)
    result = "" if feedback is None else _result_of_push(feedback)
    typed = ""
    if feedback is not None and not feedback.accepted and feedback.description:
        typed = f' value="{html.escape(feedback.description)}"'
    following = f"v{team.pushes + 1}"
    pull = (
        '<a class="pull" href="/pull" download>Pull</a>'
        if team.submitted
        else '<a class="pull" aria-disabled="true">Pull</a>'
    )
    body = f"""
    <section class="push">
      <form method="post" action="/push" enctype="multipart/form-data">
        <input type="text" name="description" class="describe"
          maxlength="{MAX_DESCRIPTION_CHARS}" aria-label="Description"
          placeholder="Description de {following} (optionnel)"{typed}>
        <label class="pick">
          <input type="file" name="file" accept=".py"
            onchange="if (this.files.length) this.form.submit()">
          Push</label>
        <noscript><button class="primary">Envoyer</button></noscript>
        {pull}
      </form>
      {result}
    </section>
    {_versions(store, team, refused)}
    """
    return tab_page(scheduler, Viewer(team), "/push", body)


def replays_page(store: Store, scheduler: Scheduler, viewer: Viewer) -> str:
    """The replays tab: the team's races, or every race for the admin."""
    body = _all_races(store) if viewer.team is None else _history(store, viewer.team)
    return tab_page(scheduler, viewer, "/replays", body)


def race_page(
    store: Store, scheduler: Scheduler, viewer: Viewer, report: RaceReport
) -> str:
    """One race, under the replays tab: its number, the replay player, and
    how it ended. A void race shows why instead of a player; its reason is
    French at the source (`runner`). A team that raced in it also gets its
    car's log in the player's top right corner, following the replay, and
    the version its car raced under the player; nobody else does."""
    mine = (
        None if viewer.team is None else store.team_race(viewer.team.key, report.index)
    )
    if report.void:
        reason = html.escape(report.void_reason or "aucune raison donnée")
        player = (
            "<div class='notice bad'><p>Course annulée : elle n'a pas de reprise "
            f"et ne compte pour personne.</p><span class='err'>{reason}</span></div>"
        )
    else:
        options = ""
        if viewer.team is not None and mine is not None:
            # The team's own car starts highlighted, so its log reads
            # against where it was on the track.
            own = {"log": f"/api/races/{report.index}/logs", "me": viewer.team.name}
            options = ", " + json.dumps(own).replace("</", "<\\/")
        player = (
            "<div id='player'></div><script>"
            "ReplayPlayer.mount(document.getElementById('player'), "
            f"'/api/races/{report.index}/replay'{options});</script>"
        )
    body = f"""
    <p class="back"><a href="/replays">&larr; Retour aux courses</a></p>
    <section>
      <h2>Course n° {report.index}</h2>
      {player}
      {_my_version(viewer, mine)}
    </section>
    {_result(report)}
    """
    return tab_page(
        scheduler,
        viewer,
        "/replays",
        body,
        title=f"Course n° {report.index}",
        wide=True,
        replay=True,
    )


def missing_race_page(scheduler: Scheduler, viewer: Viewer, index: int) -> str:
    """The page for a race number that was never run."""
    body = f"""
    <p class="back"><a href="/replays">&larr; Retour aux courses</a></p>
    <section>
      <h2>Course introuvable</h2>
      <p>Il n'y a pas de course n° {index}.</p>
    </section>
    """
    return tab_page(scheduler, viewer, "/replays", body, title="Course introuvable")


def _versions(store: Store, team: Team, refused: str | None) -> str:
    """The team's versions, newest first: each one's label, the latest
    marked as the one racing next, when it was pushed, how it did, its
    description as a form of its own, and a Pull for it. Nothing before
    the first push."""
    if not team.versions:
        return ""
    results = store.version_results(team.key)
    rows = []
    for version in reversed(team.versions):
        n = version.number
        label = version.label
        if n == team.pushes:
            label += " <span class='next'>in use</span>"
        description = html.escape(version.description, quote=True)
        rows.append(
            f"<tr><td>{label}</td><td>{_clock(version.pushed_at)}</td>"
            f"<td>{_results(results.get(n))}</td>"
            f'<td><form method="post" action="/versions/{n}/description"'
            ' class="described">'
            f'<input type="text" name="description" value="{description}"'
            f' maxlength="{MAX_DESCRIPTION_CHARS}"'
            f' aria-label="Description de {version.label}">'
            "<button>Enregistrer</button></form></td>"
            f'<td><a href="/pull?version={n}" download>Pull</a></td></tr>'
        )
    notice = (
        ""
        if refused is None
        else f"<div class='notice bad'><p>{html.escape(refused)}</p></div>"
    )
    return f"""
    <section class="versions">
      <h2>Versions</h2>
      {notice}
      <table class="log"><thead><tr><th>{VERSION}</th><th>Heure</th>
      <th>{RESULTS}</th><th>{DESCRIPTION}</th><th></th></tr></thead>
      <tbody>{"".join(rows)}</tbody></table>
    </section>
    """


def _results(results: VersionResults | None) -> str:
    """A version's results, linked to the team's races: "3 courses,
    meilleure P2", or "aucune course"."""
    if results is None:
        return "aucune course"
    count = results.races
    text = f"{count} course" + ("s" if count > 1 else "")
    if results.best is not None:
        text += f", meilleure P{results.best}"
    return f"<a href='/replays'>{text}</a>"


def _result_of_push(feedback: PushFeedback) -> str:
    """What became of the last push: "OK", since the versions table under
    it names the version in use, or "Push failed" and the trace, the store's
    reason then every error with its line. Warnings are not shown."""
    if feedback.accepted:
        return "<p class='pushed ok' id='result'>OK</p>"
    lines = [feedback.message]
    if feedback.analysis is not None:
        lines += [
            f"ligne {f.line} : {f.message}" if f.line is not None else f.message
            for f in feedback.analysis.errors
        ]
    trace = html.escape("\n".join(lines))
    return (
        "<div id='result'><p class='pushed bad'>Push failed</p>"
        f"<pre class='trace'>{trace}</pre></div>"
    )


def _my_version(viewer: Viewer, mine: TeamRace | None) -> str:
    """The version the viewer's car raced, with its description as it reads
    now, or nothing: the admin, a team not in the race."""
    if viewer.team is None or mine is None:
        return ""
    version = mine.version
    text = version.label
    if any(v.number == version.number for v in viewer.team.versions):
        text = f"<a href='/pull?version={version.number}' download>{text}</a>"
    if version.description:
        text += f" — {html.escape(version.description)}"
    return f"<p class='version'>Ta version : {text}</p>"


def _history(store: Store, team: Team) -> str:
    """Every race the team started in, newest first, each linked to its
    replay, with the version its car raced. Not the races of a deleted team
    whose name it took."""
    rows = []
    for mine in reversed(store.team_races(team.key)):
        race = mine.race
        finish = "—" if mine.finish is None else str(mine.finish)
        gain = "—" if mine.gain is None else _signed(mine.gain)
        if race.void:
            status = "<span class='bad'>annulée</span>"
        else:
            status = _status_label(mine.status, mine.fault)
        link = f"/replays/{race.index}"
        rows.append(
            f"<tr><td><a href='{link}'>{race.index}</a></td>"
            f"<td>{_KINDS[race.record.kind]}</td>"
            f"<td>{_clock(race.finished_at)}</td><td>{mine.version.label}</td>"
            f"<td class='num'>{mine.slot}</td><td class='num'>{finish}</td>"
            f"<td class='num'>{gain}</td><td>{status}</td>"
            f"<td><a href='{link}'>Revoir</a></td></tr>"
        )
    return f"""
    <section id="history" data-live>
      <table class="log"><thead><tr><th>Course</th><th>Type</th><th>Heure</th>
      <th>{VERSION}</th><th class="num">{START}</th><th class="num">{POSITION}</th>
      <th class="num">{GAIN}</th><th>{STATUS}</th><th></th></tr></thead>
      <tbody>{"".join(rows) or empty_row(9)}</tbody></table>
    </section>
    """


def _all_races(store: Store) -> str:
    """Every race of the event, newest first, with its field in grid order
    and its first classified car, each linked to its replay. The admin's
    race list. "Premier", not "Gagnant": a qualification has no winner, only
    a car that went furthest."""
    rows = []
    for race in reversed(store.races()):
        field = ", ".join(html.escape(name) for name in race.record.grid)
        if race.void:
            first = "<span class='bad'>annulée</span>"
        elif race.record.classified:
            first = html.escape(race.record.classified[0])
        else:
            first = "—"
        link = f"/replays/{race.index}"
        rows.append(
            f"<tr><td><a href='{link}'>{race.index}</a></td>"
            f"<td>{_KINDS[race.record.kind]}</td>"
            f"<td>{_clock(race.finished_at)}</td>"
            f"<td>{field}</td><td>{first}</td>"
            f"<td><a href='{link}'>Revoir</a></td></tr>"
        )
    return f"""
    <section id="races" data-live>
      <table class="log"><thead><tr><th>Course</th><th>Type</th><th>Heure</th>
      <th>Participants</th><th>{FIRST}</th><th></th></tr></thead>
      <tbody>{"".join(rows) or empty_row(6)}</tbody></table>
    </section>
    """


def _result(report: RaceReport) -> str:
    """How a race ended: the classification, with each car's grid slot and
    what the race paid it, signed (`scoring.race_gain`). An official race also shows the
    qualification points its grid was seeded from, as recorded at launch."""
    if report.void:
        return ""
    grid = report.record.grid
    points = report.qualification_points
    rows = []
    for i, name in enumerate(report.record.classified, 1):
        slot = grid.index(name) + 1 if name in grid else None
        cells = [
            f"<td>{i}</td><td>{html.escape(name)}</td>",
            f"<td class='num'>{'—' if slot is None else slot}</td>",
        ]
        if points is not None:
            cells.append(f"<td class='num'>{points.get(name, '—')}</td>")
        gain = None if slot is None else race_gain(report.record, slot, i)
        cells.append(f"<td class='num'>{'—' if gain is None else _signed(gain)}</td>")
        status = _status_label(report.statuses.get(name, ""), report.faults.get(name))
        cells.append(f"<td>{status}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    headers = [
        f"<th>{RANK}</th>",
        f"<th>{TEAM}</th>",
        f"<th class='num'>{START}</th>",
    ]
    if points is not None:
        headers.append(f"<th class='num'>{QUALIFICATION_POINTS}</th>")
    headers += [f"<th class='num'>{GAIN}</th>", f"<th>{STATUS}</th>"]
    return f"""
    <section>
      <h2>Résultat</h2>
      <table><thead><tr>{"".join(headers)}</tr></thead>
      <tbody>{"".join(rows) or empty_row(len(headers))}</tbody></table>
    </section>
    """


def _signed(gained: int) -> str:
    """A gain, signed and unfloored: "+3", "0", "-2". Qualification
    scoring floors it at zero (`scoring.qualification_points`); tables do
    not."""
    return f"+{gained}" if gained > 0 else str(gained)


def _status_label(raw: str, fault: str | None) -> str:
    """A car's status label, then its fault if it had one."""
    label = html.escape(_STATUSES.get(raw, raw))
    return label + (f"<span class='err'>{html.escape(fault)}</span>" if fault else "")


def _clock(at: float) -> str:
    """A wall-clock time of day, in the server's time zone."""
    return time.strftime("%H:%M", time.localtime(at))
