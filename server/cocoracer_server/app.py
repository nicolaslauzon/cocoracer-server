"""The event's HTTP surface.

Nothing is public but the way in. `/` is the sign-in page, one form for a
team or the admin, `/signup` creates a team, and `/start-kit.zip` is the
start kit, which the sign-in page offers before anyone has an account;
everything else needs one of the two signed in, and answers a stranger with
a redirect to `/` (the pages) or a 401 (the JSON APIs, the player's static
files and every POST).

Signed in, the site is a set of tabs, each on its own URL:

- everyone has `/leaderboard`, `/live` (the race in progress, drawn by the
  replay player from the race worker's live WebSocket) and `/replays` (a
  team's own races, or every race for the admin, each replayed at
  `/replays/{index}`),
- a team also has `/push` (a controller file, the last push's result, and
  its versions, each described at `/versions/{n}/description`) and `/pull`
  (the controller it is racing, or `?version=N` any of its versions,
  downloaded as `my_car.py`),
- the admin, on the projector, also has `/admin` (the controls, each a POST
  under `/admin/`): starting an official race, pressing its Play (on
  `/live`, where the launch leads) and pausing qualification are the
  admin's only powers over the races.

A race's recording is served on its own at `/api/races/{index}/replay`, as
the bytes the store kept (from its file, with a state directory); what a
team's car printed in it at `/api/races/{index}/logs`, to that team only; and
`/api/standings` gives the boards and the event's state as JSON: the race
running, the paused cadence and the seconds to the next qualification,
which the header's countdown ticks from.

Both kinds of sign-in work the same way: the password is checked once, and
the browser then carries a random token minted for that run of the server,
never the password, so a restart signs everyone out. Signing in as one
signs the browser out of the other.
"""

import io
import secrets
import threading
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from cocoracer_server import team_views, views
from cocoracer_server.scheduler import Scheduler
from cocoracer_server.store import (
    MAX_SOURCE_BYTES,
    TOO_LARGE,
    PushRejected,
    Rejected,
    SignedOut,
    Store,
    Team,
    TeamKey,
)
from cocoracer_server.views import Viewer

ADMIN_COOKIE = "cocoracer_admin"
TEAM_COOKIE = "cocoracer_team"
STATIC_DIR = Path(__file__).parent / "static"
# The kit in a checkout of the repository. An installed server has no
# checkout next to it, so the deploy passes the copy it installs instead.
REPO_START_KIT = Path(__file__).resolve().parents[2] / "start_kit"
# Where the kit's markdown names the server. The zip replaces it with the
# address and port the download came in on, which is one that works for the
# team, and stays right when the laptop's address changes.
SERVER_PLACEHOLDER = "{{SERVER_ADDRESS}}"


def _matches(supplied: str, secret: str) -> bool:
    """A constant-time comparison that tolerates any characters.

    `compare_digest` refuses non-ASCII strings, and a team is free to choose
    a password with an accent in it.
    """
    return bool(supplied) and secrets.compare_digest(
        supplied.encode("utf-8"), secret.encode("utf-8")
    )


def zip_start_kit(directory: Path, server: str) -> bytes:
    """The kit's files, under `start_kit/`, without caches or dotfiles, with
    `server` in place of `SERVER_PLACEHOLDER` in its markdown."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            relative = path.relative_to(directory)
            skipped = any(
                part.startswith(".") or part == "__pycache__" for part in relative.parts
            )
            if not path.is_file() or skipped:
                continue
            name = str(Path("start_kit") / relative)
            if path.suffix == ".md":
                text = path.read_text(encoding="utf-8")
                archive.writestr(name, text.replace(SERVER_PLACEHOLDER, server))
            else:
                archive.write(path, name)
    return buffer.getvalue()


def proxied_origin(request: Request) -> str | None:
    """The origin the browser reached the deploy's reverse proxy on
    ("http://name"), or None for a request that came straight to the
    server.

    The proxy marks what it forwards with X-Forwarded-Proto, whose scheme
    uvicorn has already put in the request's URL. A client that sends the
    header itself only changes the links on its own pages.
    """
    if "x-forwarded-proto" not in request.headers:
        return None
    return f"{request.url.scheme}://{request.url.netloc}"


def _secure(request: Request) -> bool:
    """Whether the browser reached this page over HTTPS, so a cookie set on
    it is never sent back in clear text."""
    return request.url.scheme == "https"


class _SignedInFiles(StaticFiles):
    """Static files for the signed-in only; `allowed` reads the request."""

    def __init__(self, allowed: Callable[[Request], bool], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._allowed = allowed

    async def get_response(self, path: str, scope: Scope) -> Response:
        if not self._allowed(Request(scope)):
            return Response(status_code=401)
        return await super().get_response(path, scope)


def create_app(
    store: Store,
    scheduler: Scheduler,
    admin_password: str,
    start_kit: Path = REPO_START_KIT,
) -> FastAPI:
    """Build the event app. Everything it needs is passed in, so tests can
    drive it with a fake runner and no background thread. `start_kit` is
    the directory `/start-kit.zip` zips."""
    app = FastAPI(title="cocoracer race server", docs_url=None, redoc_url=None)
    # The admin cookie carries this token, not the password: a browser left
    # open on the projector never holds the password the admin typed. It is
    # minted per process, so a restart signs the admin out.
    session_token = secrets.token_urlsafe(16)

    def is_admin(request: Request) -> bool:
        return _matches(request.cookies.get(ADMIN_COOKIE) or "", session_token)

    def require_admin(request: Request) -> None:
        if not is_admin(request):
            raise HTTPException(status_code=401, detail="admin password required")

    Admin = Annotated[None, Depends(require_admin)]

    # A team's session token -> the team's key, its sign-up: if the admin
    # deletes the team and someone else claims the name, the old browser is
    # signed out rather than handed the new team. The store checks the key
    # again under its own lock on every per-team call, so a deletion that
    # lands after `signed_in` reaches the action as `SignedOut`.
    team_sessions: dict[str, TeamKey] = {}
    sessions_lock = threading.Lock()

    def signed_in(request: Request) -> Team | None:
        token = request.cookies.get(TEAM_COOKIE) or ""
        with sessions_lock:
            key = team_sessions.get(token)
        return None if key is None else store.team(key)

    def viewer(request: Request) -> Viewer | None:
        """Who is signed in: a team, the admin, or nobody."""
        team = signed_in(request)
        if team is not None:
            return Viewer(team)
        if is_admin(request):
            return Viewer()
        return None

    def home() -> RedirectResponse:
        return RedirectResponse("/", status_code=303)

    def sign_in(request: Request, team: Team) -> RedirectResponse:
        token = secrets.token_urlsafe(16)
        with sessions_lock:
            team_sessions[token] = team.key
        response = home()
        response.set_cookie(
            TEAM_COOKIE,
            token,
            httponly=True,
            samesite="lax",
            secure=_secure(request),
        )
        response.delete_cookie(ADMIN_COOKIE)
        return response

    # Why a team's last description edit was refused, shown once on its push
    # tab. It is not the store's push feedback: no push was made. Keyed by
    # the team's key, so a team that takes a deleted team's name never sees
    # it.
    refused_descriptions: dict[TeamKey, str] = {}
    refused_lock = threading.Lock()

    def forget_team(request: Request) -> None:
        with sessions_lock:
            team_sessions.pop(request.cookies.get(TEAM_COOKIE) or "", None)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Response:
        """The sign-in page, or the signed-in viewer's first tab."""
        who = viewer(request)
        if who is None:
            return HTMLResponse(views.login_page())
        return RedirectResponse(who.tabs[0][0], status_code=303)

    @app.get("/signup", response_class=HTMLResponse)
    def signup_form(request: Request) -> Response:
        if viewer(request) is not None:
            return home()
        return HTMLResponse(views.signup_page())

    @app.post("/signup")
    def signup(
        request: Request,
        team: Annotated[str, Form()],
        password: Annotated[str, Form()],
    ) -> Response:
        try:
            signed_up = store.register(team, password)
        except Rejected as exc:
            return HTMLResponse(
                views.signup_page(error=exc.detail, name=team),
                status_code=exc.status_code,
            )
        return sign_in(request, signed_up)

    @app.post("/login")
    def login(
        request: Request,
        password: Annotated[str, Form()],
        role: Annotated[str, Form()] = "team",
        team: Annotated[str, Form()] = "",
    ) -> Response:
        """One form for both: `role` says whether it is a team or the admin,
        and the admin's sign-in ignores the team name."""
        if role == "admin":
            if not _matches(password, admin_password):
                # Only the admin sees this refusal, so it is in English,
                # like the Admin tab, on an otherwise French page.
                return HTMLResponse(
                    views.login_page(role="admin", error="Wrong admin password."),
                    status_code=401,
                )
            forget_team(request)
            response = home()
            response.set_cookie(
                ADMIN_COOKIE,
                session_token,
                httponly=True,
                samesite="lax",
                secure=_secure(request),
            )
            response.delete_cookie(TEAM_COOKIE)
            return response
        found = store.authenticate(team, password)
        if found is None:
            return HTMLResponse(
                views.login_page(
                    error="Nom d'équipe ou mot de passe incorrect.", name=team
                ),
                status_code=401,
            )
        return sign_in(request, found)

    @app.post("/logout")
    def logout(request: Request) -> RedirectResponse:
        forget_team(request)
        response = home()
        response.delete_cookie(TEAM_COOKIE)
        response.delete_cookie(ADMIN_COOKIE)
        return response

    @app.get("/leaderboard", response_class=HTMLResponse)
    def leaderboard(request: Request) -> Response:
        who = viewer(request)
        if who is None:
            return home()
        return HTMLResponse(views.leaderboard_page(store, scheduler, who))

    @app.get("/replays", response_class=HTMLResponse)
    def replays(request: Request) -> Response:
        who = viewer(request)
        if who is None:
            return home()
        return HTMLResponse(team_views.replays_page(store, scheduler, who))

    @app.get("/replays/{index}", response_class=HTMLResponse)
    def race(request: Request, index: int) -> Response:
        who = viewer(request)
        if who is None:
            return home()
        report = store.race(index)
        if report is None:
            return HTMLResponse(
                team_views.missing_race_page(scheduler, who, index),
                status_code=404,
            )
        return HTMLResponse(team_views.race_page(store, scheduler, who, report))

    @app.get("/push", response_class=HTMLResponse)
    def push_tab(request: Request) -> Response:
        team = signed_in(request)
        if team is None:
            return home()
        with refused_lock:
            refused = refused_descriptions.pop(team.key, None)
        return HTMLResponse(team_views.push_page(store, scheduler, team, refused))

    @app.post("/push")
    def push(
        request: Request,
        file: Annotated[UploadFile | None, File()] = None,
        description: Annotated[str, Form()] = "",
    ) -> Response:
        """Push a controller file as the team's next version, with the
        description typed beside it, if any.

        Whatever the outcome, the answer is a redirect back to the push tab,
        where the store's feedback for the push is shown. No file at all is
        pushed as an empty controller, which the store refuses.
        """
        team = signed_in(request)
        if team is None:
            return home()
        back = RedirectResponse("/push", status_code=303)
        source = ""
        try:
            if file is not None and file.filename:
                # One byte over the cap is enough to know it is too large,
                # without reading an arbitrarily large upload into memory.
                raw = file.file.read(MAX_SOURCE_BYTES + 1)
                if len(raw) > MAX_SOURCE_BYTES:
                    store.refuse(team.key, TOO_LARGE, description=description)
                    return back
                try:
                    source = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    store.refuse(
                        team.key,
                        "Le fichier n'est pas du texte UTF-8.",
                        description=description,
                    )
                    return back
            store.push(team.key, source, description)
        except SignedOut:
            return home()
        except PushRejected:
            pass  # the store kept the reason, and the page shows it
        return back

    @app.post("/versions/{number}/description")
    def describe(
        request: Request, number: int, description: Annotated[str, Form()] = ""
    ) -> Response:
        """Change the description of one of the team's own versions; empty
        clears it. The answer is a redirect back to the push tab, which
        shows a refusal."""
        team = signed_in(request)
        if team is None:
            return home()
        try:
            store.describe(team.key, number, description)
        except SignedOut:
            return home()
        except Rejected as exc:
            with refused_lock:
                refused_descriptions[team.key] = exc.detail
        return RedirectResponse("/push", status_code=303)

    @app.get("/pull")
    def pull(request: Request, version: int | None = None) -> Response:
        """Download the controller the team is racing, or its version
        `version`, as `my_car.py`.

        A 404 before the first push, and for a version the team does not
        have.
        """
        team = signed_in(request)
        if team is None:
            return home()
        if not team.submitted:
            return Response("No controller pushed yet.", status_code=404)
        try:
            source = store.version_source(
                team.key, team.pushes if version is None else version
            )
        except SignedOut:
            return home()
        if source is None:
            return Response("No such version.", status_code=404)
        return Response(
            source,
            media_type="text/x-python; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="my_car.py"'},
        )

    @app.get("/start-kit.zip")
    def start_kit_zip(request: Request) -> Response:
        """The start kit, zipped on every request, so an edit to the kit
        needs no restart, naming the server by the address and port this
        request reached it on. Behind the deploy's proxy that address is
        the proxy's origin, scheme included. Open to everyone; a 404 if
        the server has none."""
        if not start_kit.is_dir():
            return Response("No start kit on this server.", status_code=404)
        server = proxied_origin(request) or request.url.netloc
        return Response(
            zip_start_kit(start_kit, server),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="start_kit.zip"'},
        )

    @app.get("/live", response_class=HTMLResponse)
    def live(request: Request) -> Response:
        """The live race, for everyone. The player connects to the race
        worker on the host name this page was reached by, which is the
        machine running both, or through the proxy the page came through."""
        who = viewer(request)
        if who is None:
            return home()
        host = request.url.hostname or "127.0.0.1"
        return HTMLResponse(
            views.live_page(scheduler, who, host, proxied_origin(request))
        )

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(request: Request) -> Response:
        """The admin tab."""
        if not is_admin(request):
            return home()
        return HTMLResponse(views.admin_page(store, scheduler))

    @app.get("/api/races/{index}/replay")
    def race_replay(request: Request, index: int) -> Response:
        """One race's recording, the only place a replay is served from.

        Served as stored, never serialised again: a replay is a few MB.
        """
        if viewer(request) is None:
            raise HTTPException(status_code=401, detail="sign in to watch replays")
        replay = store.replay(index)
        if replay is None:
            raise HTTPException(status_code=404, detail="no replay for this race")
        # A finished race never changes, so the browser may keep it.
        headers = {"Cache-Control": "private, max-age=86400"}
        if isinstance(replay, Path):
            return FileResponse(replay, media_type="application/json", headers=headers)
        return Response(replay, media_type="application/json", headers=headers)

    @app.get("/api/races/{index}/logs")
    def race_log(request: Request, index: int) -> Response:
        """What the signed-in team's car printed in one race, as
        `{"lines": [[t, text], ...], "truncated"}`; no lines if it printed
        nothing. Only a team's own car: nobody reads another team's log, the
        admin included, whose screen is the projector.

        A car that is not the team's gets nothing (`Store.log`), so a name
        taken again after a deletion never reads the old team's log.
        Never cached: one browser can sign in as another team.
        """
        team = signed_in(request)
        if team is None:
            raise HTTPException(status_code=401, detail="sign in as a team")
        report = store.race(index)
        if report is None:
            raise HTTPException(status_code=404, detail="no such race")
        log = store.log(index, team.key)
        return JSONResponse(
            log or {"lines": [], "truncated": False},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/standings")
    def api_standings(request: Request) -> dict[str, Any]:
        """The boards as JSON, and the event's state, which the header's
        status polls: the race running, the paused cadence, and the seconds
        to the next qualification (None while paused or not scheduled)."""
        if viewer(request) is None:
            raise HTTPException(status_code=401, detail="sign in first")
        remaining = scheduler.seconds_to_next_race
        return {
            "paused": scheduler.paused,
            "running": scheduler.running_race,
            "next_race_in": None if remaining is None else round(remaining, 1),
            "races": len(store.races()),
            "standings": [
                {
                    "team": s.team,
                    "qualification_score": s.qualification_score,
                    "official_score": s.official_score,
                    "best_official": s.best_official,
                }
                for s in store.standings()
            ],
        }

    @app.post("/admin/pause")
    def admin_pause(_: Admin) -> RedirectResponse:
        scheduler.pause()
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/resume")
    def admin_resume(_: Admin) -> RedirectResponse:
        scheduler.resume()
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/official-race")
    def admin_official(_: Admin) -> RedirectResponse:
        # The race runs in its own thread: this request must come back now,
        # not in five minutes, and a second click is refused rather than
        # queued behind the race already running. A launch takes the admin
        # to Live, to watch the race they started (on the projector, too).
        if not scheduler.start_official():
            raise HTTPException(status_code=409, detail="a race is already running")
        return RedirectResponse("/live", status_code=303)

    @app.post("/admin/play")
    def admin_play(_: Admin) -> RedirectResponse:
        # The launched official race waits on the grid until this; the
        # countdown, and the lights on every Live page, start now. A press
        # with nothing waiting (a double click, a stale tab) changes nothing
        # and lands on Live all the same.
        scheduler.play()
        return RedirectResponse("/live", status_code=303)

    @app.post("/admin/lock-signups")
    def admin_lock_signups(_: Admin) -> RedirectResponse:
        store.lock_signups()
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/unlock-signups")
    def admin_unlock_signups(_: Admin) -> RedirectResponse:
        store.unlock_signups()
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/delete-team")
    def admin_delete(_: Admin, team: Annotated[str, Form()]) -> RedirectResponse:
        store.delete(team)
        return RedirectResponse("/admin", status_code=303)

    app.mount(
        "/static",
        _SignedInFiles(lambda r: viewer(r) is not None, directory=STATIC_DIR),
        name="static",
    )
    return app
