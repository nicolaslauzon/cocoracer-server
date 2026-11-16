# The event's state persists on disk, as one JSON file

The server used to keep the whole event in memory, so one restart erased
it: every account, submission and race, and with the races the
qualification standings that seed the official grid. Anything restarts the
service — a crash and `Restart=on-failure`, an OOM kill, an admin re-running
`deploy.sh` to change a port, a reboot, a closed lid — and at a three-hour
event with sixteen teams, losing the afternoon's standings is not
recoverable. So the store now saves itself.

Given `--state-dir DIR` (the deploy always passes `/var/lib/cocoracer-server`, its
`StateDirectory`), `Store` rewrites `DIR/state.json` after every mutation
that changes what is kept: a sign-up, an accepted push, a deletion, a
recorded race, the freeze and the unfreeze. It writes a temp file in the
same directory, `fsync`s it, `os.replace`s it over the old one and `fsync`s
the directory, so a crash at any point leaves either the old file or the new
one, never half of either. At startup it loads the file back. The file is
small (sixteen teams' sources, capped at 256 KB each, and a few dozen race
reports), so rewriting all of it each time costs milliseconds against a
cadence of minutes.

What it keeps: each team's name, password hash (never the password), sign-up
time, source, last push time and push count; every race report except its
replay; the freeze. What a restart deliberately drops:

- **Sessions.** Tokens are minted per run, so everyone signs in again. That
  costs a few seconds per team and keeps secrets out of the file.
- **The last push's feedback.** It only says how the last push went; the
  submission it produced is kept.
- **A race in flight.** It is never recorded; the next race runs as usual.
- **The qualification timer.** A fresh full interval starts at boot rather
  than a race firing the moment the server comes back.

A frozen event comes back frozen, since the official races must all judge
the same code.

A state file that cannot be read, or carries a version other than
`STATE_VERSION` (2), stops the server with a message naming the file. It never
starts empty over it: a silently empty leaderboard would be discovered
after teams had signed up again, too late to recover the file. Wiping is an
explicit act, `deploy.sh --wipe-state`, and there is no wipe button on the
web page.

## Considered options

- **In memory only** (the old design, rejected): the cheapest, and the one
  that made a routine restart fatal to the event.
- **SQLite** (rejected): transactions and partial updates the store does not
  need. The state is at most a few megabytes, read once and written whole,
  and every mutation already goes through one lock in `Store`, so a database
  would add a schema and a second model of the same data for no gain.
- **An append-only journal** (rejected): cheaper writes, but replay logic,
  compaction and a torn last line to handle, all to save milliseconds.
- **One JSON file, rewritten atomically after every mutation** (chosen): no
  dependency, readable with `less` in the middle of the event, and atomic by
  construction.

## Consequences

- Without `--state-dir` nothing is written, so the tests and a dev run are
  unchanged and cannot leave files behind.
- Serialisation lives next to the dataclasses (`Team.to_json`,
  `RaceReport.to_json` and their `from_json`). A change to what they hold
  must change the file format too, and a change the old reader cannot parse
  bumps `STATE_VERSION`.
  Only the current version is read: no old file is migrated, and the event
  starts from a wiped state.
- A save that fails mid-event (a full disk) does not fail the request that
  caused it: the change stays in memory, the error goes to the journal, and
  the admin page shows it until a later save succeeds.
- Replays are not in `state.json`: they are the bulk of the data and never
  change once written, so each is written once, atomically, to
  `replays/<index>.json` beside it, and served from that file. Memory keeps
  only which races have one. Without a state directory the store keeps each
  replay as the bytes the runner serialised, never re-serialised per
  request.
- The deploy's filesystem is no longer entirely read-only: the service user
  can write `/var/lib/cocoracer-server`, and so can the team code it runs, which
  could tamper with the saved state. That is within the accepted shared
  sandbox (ADR 0002).
