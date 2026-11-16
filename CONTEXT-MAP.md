# Context Map

This repo has three contexts:

- **cocoracer** (`cocoracer/`): the racing game engine and CLI — players program an autonomous controller and race it against baselines and other controllers. It's a git submodule with its own repo. See `cocoracer/CONTEXT.md` and `cocoracer/docs/adr/`.
- **server** (`server/`): the event server — teams push their controller to it, it races the whole field on a cadence (`scheduler.QUALIFICATION_INTERVAL`), and it keeps the two leaderboards that decide the challenge. See `server/CONTEXT.md`.
- **start_kit** (`start_kit/`): what a team has initially to start the challenge. It has no `CONTEXT.md`: the kit is only what ships to teams.

## Relationships

- **start_kit → cocoracer**: the kit does not install cocoracer, which never leaves the server. It ships `cocoracer.py`, a physics-free copy of the controller API (`Controller`, `TrackInfo`, `CenterlinePoint`), so `from cocoracer import Controller, TrackInfo` in `my_car.py` type-checks on a laptop and reaches the real package on the server. `server/tests/test_start_kit.py` keeps the copy in step with the package. The kit's `centerline.csv` is the event track's centerline, written by `scripts/write_centerline_csv.py`; the same test fails when it is stale. Teams cannot race locally.
- **start_kit → server**: through a browser only. A team opens the address in `wtf.md`, signs up and pushes `my_car.py` from the server's page; no tool reads the file. The other way, the server hands out the kit itself: `/start-kit.zip`, open to everyone and linked from the sign-in page and every tab's header, zips the directory `--start-kit` names on every request, with the address the request came in on in place of `wtf.md`'s `{{SERVER_ADDRESS}}`, which `deploy.sh` installs as `/opt/cocoracer-server/start_kit`.
- **server → cocoracer**: the server imports cocoracer to race a field, in a subprocess it can kill (`docs/adr/0002-race-in-a-killable-subprocess.md`). A cocoracer change reaches the event server along these lines:
  - param defaults (`config.DEFAULT_PARAMS`, the `l` track the event races on among them) → `scheduler` and `race_worker`;
  - the engine, track, classification and live view (`web`) → `race_worker`;
  - the controller API and loader (`controller`) and `field`'s command and fault rule → `car_process` and `car_field`, the worker's adapter that puts the server's car processes behind `field`, the seam the engine steps a race's controllers through (cocoracer `docs/adr/0007-*`);
  - the names a controller may import from `cocoracer` → `analysis`, which refuses any other at push time, and the start kit's `cocoracer.py`.
- **server → cocoracer's live view**: during a live race the worker serves cocoracer's live view to the LAN (every interface, on the race port), and the server's Live tab draws the race from that view's WebSocket, so cocoracer's live message format is a contract with the server's player (`static/replay.js`). The view is read-only: the worker paces the race itself (a qualification's pre-roll on the grid included) and hands the view no start queue, so it ignores what viewers send.
- **Field** is one concept seen from both sides: the server's field is the teams entered in a race; cocoracer's is the same race's controllers, one per grid slot, as its engine reaches them. The race worker turns the one into the other, a car process per team, in grid order (`race_worker.field`).
- The split of responsibility for a misbehaving controller: cocoracer contains one that **raises**, the server contains one that **hangs**.

System-wide decisions go in `docs/adr/` at the repo root. Context-scoped decisions go in `<context>/docs/adr/`.
