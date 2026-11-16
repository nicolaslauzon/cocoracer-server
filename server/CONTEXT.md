# server

The event server for the racing challenge: teams push their controller to it,
it races every team's latest controller on a fixed cadence, and it keeps the
two leaderboards that decide the winner.

## Language

**Team**:
A named competitor with a password it chose and the versions of its
controller it pushed. Created on the sign-up page, with no submission, unless the admin has
locked sign-ups; it joins the field with its first accepted push. No two teams share a name at once,
but a team is its sign-up, not its name: once the admin deletes a team, its
name is free, and a team that signs up with it starts clean. It owns only
the races its own cars ran, after it signed up; the deleted team's races
stay in the history under the name, and still count for the other teams,
but none of them is the new team's: no points, no results, no replays in
its list, no log, no version. There is no password recovery: the admin
deletes the team.
_Avoid_: player, user, account, competitor

**Push**:
One submission of a controller's source by a signed-in team, through its
push tab, as a file, with an optional description. It is gated by the
analysis: errors refuse it and the team's previous submission keeps racing;
warnings are shown and it goes through. An accepted push becomes the team's
next version; a refused one uses up no number.
_Avoid_: upload, submit, commit, deploy

**Analysis**:
The static checks a push goes through before it is accepted. It reads the
source and never runs it, and it reports errors, which refuse the push, and
warnings, which don't. A controller that loops forever or raises at runtime
gets past it; the race still handles those.
_Avoid_: validation, lint, review

**Replay**:
The recording of one race, sampled ten times a second of simulated time,
that any signed-in team, or the admin, can watch again from the replays
tab. A void race has none.
_Avoid_: video, playback, rerun

**Log**:
What one car's controller printed during one race (its import, `reset` and
every `step`), each line stamped with the race time it was printed at. Only
the car's own team reads it, in the corner of the race's replay; not the admin, whose
screen is the projector. A void race has none.
_Avoid_: console, output, trace

**Live view**:
The race in progress, as cocoracer serves it from the race worker on the
race port of every interface, so the whole venue LAN reaches it; the
deploy's reverse proxy serves it at `/live/ws` beyond the LAN. Everyone
signed in, teams and admin alike, watches it in the Live tab, whose player
reads the view's WebSocket. It is read-only: a qualification waits on the
grid for `runner.PREROLL` (2 s), counted from its socket being up, then
starts by itself, so a viewer who joins as it comes up sees its whole
countdown; an official race waits on the grid until the admin's
Play (a button only the admin sees on Live); and nothing a viewer sends
reaches it. Either way the replay opens at the release. The player tries
the socket only while the header's status says a race runs, and the Live
tab polls that status every second. Everyone sees the start lights.
_Avoid_: stream, broadcast, projection

**Version**:
One accepted push of a team's controller, kept for the rest of the event and
labelled by its number for that team, `v1`, `v2`, `v3`... The label is
automatic and never reused or renumbered, so `v3` always means the same
code; no version is ever deleted, except with its team. A team may describe
a version, when pushing it or later; the description is optional. A team's
versions and their descriptions are private to it, like its log: not even
the admin sees them. Each race records the version each car raced, as it was
when the field was snapshotted, and the team reads its own on the race's
replay. The push tab lists the team's versions, with how many races each
ran and its best finish, and the team can pull any of them back.
State saved before versions existed is not loaded: the event starts from a
wiped state (`deploy.sh --wipe-state`), so every race records each car's
version.
_Avoid_: revision, release, commit, tag

**Submission**:
A team's latest version: the controller it will race next. Only a push
changes it; there is no racing an older version except by pushing it again,
as a new one.
_Avoid_: entry, revision

**Field**:
The set of teams entered in a race, snapshotted when that race starts. A push
that lands mid-race belongs to the next one.
_Avoid_: roster, lineup, entrants

**Qualification race**:
The automatic race held on the cadence: the whole field, from a randomly
shuffled grid, for one minute with no lap target. Nobody finishes on laps; at
the minute every car still out is done, and the field is classified by how far
each car went. Every race runs on cocoracer's `l` track.
_Avoid_: practice, heat, warm-up, qualifying session

**Official race**:
One of the admin-triggered races that decide the challenge: the whole field,
three laps, from a grid seeded by qualification score. It ends 30 s after the
first car completes its laps, or at the five-minute limit, whichever comes
first; the cars still out then are done, ranked by distance behind the
finishers.
_Avoid_: final, main event, real race

**Qualification score**:
A team's points summed over the event's last eight qualification races: in
each, its net passes, floored at zero, so nothing is lost for being passed.
It seeds the official grid. A qualification recorded before passes were
counted pays its positions gained instead.
_Avoid_: practice points, ranking, qualifying time

**Pass**:
One car's lead over another, measured in distance along the track, climbing
through a whole number of laps: through zero is an overtake, through one lap
is lapping the other car, whatever its status. Dropping back through one
takes a pass back. A car's net passes in a race are summed over every other
car; without lapping they equal its positions gained, unfloored.
_Avoid_: overtake count, moves

**Official score**:
The sum of a team's finishing positions across the official races. The
lowest wins the challenge. An official race the team missed (it joined the
field after it) counts as last in it: that race's field size plus one.
_Avoid_: average rank, mean, final points

**Positions gained**:
A team's starting slot minus its finishing position in one race. An official
race's tables show it; a qualification's show its net passes.
_Avoid_: places made up, delta, overtakes

**Classified**:
The full field of one race in finishing order: finishers by finish time,
then every other car, done or DNF, by how far it got. Scoring only ever reads this order.
_Avoid_: results, standings, finish order

**Void race**:
A race the server could not finish: nobody on the grid, its race process
failing, or the race falling so far behind real time that it overran its
wall-clock budget. No controller can void one: whatever a controller does,
a hang included, costs its own car alone. It is kept in the history, with
its reason in French, and scores nothing for anybody.
_Avoid_: failed race, aborted, cancelled

**Sign-up lock**:
The state, set and lifted by the admin, in which the server creates no new
team. It changes nothing for the teams already signed up: they push and race
as before. Pushes are never closed, official races included; a race judges
the sources its field was snapshotted with, so a push lands in the next race.
_Avoid_: freeze, lockout, registration closed

**Cadence**:
The rhythm of qualification races, one every `scheduler.QUALIFICATION_INTERVAL`
seconds, which the admin can pause and resume.
_Avoid_: schedule, timer, loop

**Admin**:
The single operator of the event, authenticated by one password, who triggers
official races, pauses the cadence, locks sign-ups and deletes teams. The
password is checked once at sign-in and the browser then carries a session
token minted for that run of the server, so a restart signs the admin out.
_Avoid_: organizer, host, moderator
