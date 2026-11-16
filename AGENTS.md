# cocoracer-server

## Testing and checks

Two packages, two venvs. Run the checks of the one whose code you touched
(or both) before every commit. cocoracer's four checks are in
`cocoracer/AGENTS.md`. The server's, from `server/`:

```bash
.venv/bin/ruff format .
.venv/bin/ruff check .
.venv/bin/mypy cocoracer_server tests
PYTHONPATH= setsid -w .venv/bin/python -m pytest   # ~45s
```

`setsid -w` puts the suite in its own process group: its car-process tests
attack the sandbox for real, and once, against an unfixed filter, a test
car's `setpriority(PRIO_USER, ...)` reniced the whole desktop session. A test
that probes an escape must use values that change nothing if the escape
works. `PYTHONPATH=` drops a ROS `site-packages` that breaks pytest plugin
loading (`No module named 'lark'`).

The server follows the Formatting, Type hints and Naming sections of
`cocoracer/docs/coding-style.md`, except that it targets Python 3.12
(ruff `target-version = "py312"`, matching its `requires-python`); that
file's Module layout and Docstrings sections are cocoracer's own.

There is no slow tier. Keep it that way: no test races a controller to the
end of a real race, and race behaviour itself is verified by hand. The
server's car tests drive real car processes on the short oval in
`tests/mini_race.py`; cocoracer's equivalent rule is in `cocoracer/AGENTS.md`.
The deliberate slow tests (1 to 5s) are `tests/test_cadence_end_to_end.py`,
which paces a live qualification's countdown against the wall clock, and
`tests/test_runner.py`, which tests the subprocess boundary on the shipped
stadium. Check a new test against them with `--durations=10`.

`cocoracer` is a git submodule. Commit there first, then commit the bumped
pointer in this repo; push cocoracer before this repo.

## Human language (French/English)

Three things are French, because teams read them: the markdown in
`start_kit/` (`wtf.md`); the server's web pages
(`server/cocoracer_server/views.py`, `team_views.py`, and the replay player's
labels in `static/replay.js`); and every message those pages show a team,
such as the analysis findings (`analysis.py`) and the account and push
refusals (`store.py`, and those `app.py` puts on a page; its JSON API errors
stay English). That French says "tu" to a team (ton, ta, tes, toi, and
imperatives in the tu form), never "vous", in the casual tone of `wtf.md`.
Inside that French, commands, file names and
the controller API keep their English spelling, as the code has them. The
tab labels (Classement, Live, Replay, Push, Admin), the push tab's Push
and Pull buttons, the "in use" mark on the push tab's latest version, and
the replay log's Wrap checkbox are deliberately
English: the maintainer chose them, so
leave them as they are. So are three car
statuses, kept in racing English: "DNF", "pause" and "ghost" (the others are
"en course" and "terminé"); `team_views._STATUSES` and `STATUS_LABELS` in
`static/replay.js` hold the same strings.

The maintainer made a second exception: the Admin tab (`admin_page` in
`views.py`, its header included, except the header's event status, which is
French everywhere) and every message only the admin sees,
such as the admin sign-in refusal in `app.py`, are English. The tabs the
admin shares with teams (Classement, Live, Replay) stay French, because they
go on the projector. Write "admin", never "administration", in either
language.

Everything else is English: code, comments, docstrings, config, CLI and
error output, tests, maintainer docs. That includes every non-markdown file in
`start_kit/`.

## Agent skills

### Issue tracker

Issues live as local markdown files under `.scratch/<feature-slug>/`, git-ignored and not in the repo. See `docs/agents/issue-tracker.md`.

### Domain docs

Multi-context: root `CONTEXT-MAP.md` points to per-context `CONTEXT.md` files (`cocoracer/`, `server/`). See `docs/agents/domain.md`.
