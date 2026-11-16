"""Start the event server.

    cocoracer-server --host 0.0.0.0 --port 8080

Binds every interface by default so the teams on the venue LAN can reach it.
The live race view always binds every interface, on `--race-port`, since
the Live tab of every team's page reads it.
The admin password comes from COCORACER_ADMIN_PASSWORD; if it is unset a random
one is generated and printed once at startup, so the page is never left open
behind a guessable default.

With `--state-dir DIR` the event (teams, submissions, races, the sign-up lock) is
saved to `DIR/state.json` after every change and restored from it at
startup; without it the event lives in memory and a restart erases it. A
state file that cannot be read stops the server rather than being replaced.

`--start-kit DIR` is the directory the sign-in page offers as a zip; it
defaults to the `start_kit/` of the checkout the server runs from.
"""

import argparse
import os
import secrets
import sys
from pathlib import Path

import uvicorn

from cocoracer_server.app import REPO_START_KIT, create_app
from cocoracer_server.runner import RACE_VIEW_PORT
from cocoracer_server.sandbox import make_undumpable
from cocoracer_server.scheduler import (
    DEFAULT_TRACK,
    QUALIFICATION_INTERVAL,
    Scheduler,
)
from cocoracer_server.store import MAX_TEAMS, StateError, Store

ADMIN_PASSWORD_ENV = "COCORACER_ADMIN_PASSWORD"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cocoracer-server", description="Event server for the racing challenge."
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--track", default=DEFAULT_TRACK, help="track every race runs on"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=QUALIFICATION_INTERVAL,
        help="seconds between qualification races (default 120)",
    )
    parser.add_argument(
        "--max-teams", type=int, default=MAX_TEAMS, help="hard cap on registrations"
    )
    parser.add_argument(
        "--no-cadence",
        action="store_true",
        help="start with the qualification cadence paused",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="simulate races as fast as possible instead of pacing them to "
        "the wall clock behind the live view (the Live tab stays empty)",
    )
    parser.add_argument(
        "--race-port",
        type=int,
        default=RACE_VIEW_PORT,
        help=f"port the live race view is served on, on every interface "
        f"(default {RACE_VIEW_PORT}); the pages' Live tab reads it, so it must "
        f"be reachable from the teams' machines",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        help="directory to save the event in (state.json) and restore it from "
        "at startup; without it the event lives in memory only",
    )
    parser.add_argument(
        "--start-kit",
        type=Path,
        default=REPO_START_KIT,
        help="directory the pages offer for download as the start kit "
        "(default: the start_kit/ of this checkout)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        store = Store(max_teams=args.max_teams, state_dir=args.state_dir)
    except StateError as exc:
        print(f"cocoracer-server: {exc}", file=sys.stderr)
        print(
            "  refusing to start over it; move the file aside to start an empty event",
            file=sys.stderr,
        )
        return 1
    except OSError as exc:
        print(
            f"cocoracer-server: cannot write the state in {args.state_dir}: {exc}",
            file=sys.stderr,
        )
        return 1
    password = os.environ.get(ADMIN_PASSWORD_ENV)
    # The cars run as this user: keep them out of /proc/<this pid>/environ,
    # which still holds the password, and the rest of this process's files.
    make_undumpable()
    if not password:
        password = secrets.token_urlsafe(9)
        print(f"admin password: {password}  (set {ADMIN_PASSWORD_ENV} to choose one)")
    if store.state_file is None:
        print("state: in memory only; a restart erases the event")
    elif store.restored_at is not None:
        print(
            f"state: restored {len(store.accounts())} teams and "
            f"{len(store.races())} races from {store.state_file}"
        )
    else:
        print(f"state: saved to {store.state_file}")
    scheduler = Scheduler(
        store,
        track=args.track,
        interval=args.interval,
        live=not args.headless,
        port=args.race_port,
    )
    app = create_app(
        store=store,
        scheduler=scheduler,
        admin_password=password,
        start_kit=args.start_kit,
    )
    if not args.start_kit.is_dir():
        print(f"start kit: {args.start_kit} is not a directory; its download is a 404")
    scheduler.start()
    if args.no_cadence:
        scheduler.pause()
    print(f"sign in at http://{args.host}:{args.port}")
    print(
        "  teams sign up and push there; the admin signs in there too, choosing Admin"
    )
    if not args.headless:
        print(
            f"races are served live on port {args.race_port} of every "
            f"interface; everyone watches them in the Live tab"
        )
    try:
        # Behind the deploy's reverse proxy, on this machine, a request's
        # scheme and client are the ones the proxy forwards: the pages then
        # link the proxy's origin (and over https://, set secure cookies). Only
        # loopback is trusted to say so.
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="warning",
            proxy_headers=True,
            forwarded_allow_ips="127.0.0.1",
        )
    finally:
        scheduler.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
