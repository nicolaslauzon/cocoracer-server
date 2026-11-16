"""The event's pages, as strings: the sign-in, and the tabs.

Nothing is public. Signed out, the site is `login_page` (one form, as a team
or as the admin, and the start kit's download) and `signup_page`. Signed in,
every page is a tab under the same header — the event title, the event's
status, who is signed in, the start kit, the sign-out button and the tabs —
built by `tab_page` for a `Viewer`.

This module has the frame and the tabs about the event as a whole: the
leaderboard (`leaderboard_page`), the admin controls (`admin_page`) and the
live race (`live_page`), which every viewer has. The tabs about a team's code and races — the push
and the replays — live in `team_views`, and share this module's frame:
`tab_page`, the palette in `_CSS`, the `boards`, `count` and `empty_row`.

Everything here is a pure function of the store and the scheduler: no
framework, no request, no I/O. The pages are rendered whole on every request
rather than served from a template engine — there are a handful of them,
they change once a race, and a projector at the back of the
room is the client that matters most.

The pages are in French, for the room; the code and its tests stay English.
Two exceptions, both the maintainer's choice: the tab labels, and the Admin
tab, which only the admin reads and is English throughout, its header
included (`tab_page(..., english=True)`), except the header's event status,
which is French on every tab.

No page ever reloads itself, so a click or a picked file is never lost: a
small script fetches the page again every few seconds and swaps in the parts
marked `data-live` (the boards and the race lists). The header's status is
kept by a script of its own, which polls `/api/standings` and ticks the
countdown to the next qualification every second.
"""

import html
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass

from cocoracer_server.scheduler import Scheduler
from cocoracer_server.scoring import (
    QUALIFICATION_WINDOW,
    RaceKind,
    Standing,
    by_qualification_score,
)
from cocoracer_server.store import Store, Team

REFRESH_SECONDS = 10
# How often the header's status asks the server for the event's state; in
# between, the countdown ticks on the client. Every second on the Live tab,
# whose player connects to a race's socket as soon as the poll sees it run.
STATUS_POLL_SECONDS = 5
LIVE_STATUS_POLL_SECONDS = 1
EVENT_TITLE = "Défi vroom vroom"
# Where the deploy's reverse proxy serves the race worker's live WebSocket,
# on the pages' own origin; it forwards it to the worker's /ws.
LIVE_PROXY_PATH = "/live/ws"

# The event's status, in French on every tab, Admin included: it is the
# same line for everyone in the room. `_STATUS_SCRIPT` carries it.
_STATUS_TEXT = {
    "paused": "qualifications en pause",
    "next": "prochaine qualif dans ",
    RaceKind.QUALIFICATION.value: "course de qualification en cours",
    RaceKind.OFFICIAL.value: "course officielle en cours",
}
# The rest of the header's words, by page language: French for the room,
# English on the Admin tab.
_HEADER_TEXT = {
    "fr": {
        "sign_out": "Se déconnecter",
        "tabs": "Onglets",
        "kit": "Kit de départ",
        "kit_lines": "Kit de<br>départ",
    },
    "en": {
        "sign_out": "Sign out",
        "tabs": "Tabs",
        "kit": "Start kit",
        "kit_lines": "Start<br>kit",
    },
}

# Each viewer's tabs, as (path, label), in header order. The first is where
# signing in lands.
TEAM_TABS = (
    ("/leaderboard", "Classement"),
    ("/live", "Live"),
    ("/replays", "Replay"),
    ("/push", "Push"),
)
ADMIN_TABS = (
    ("/leaderboard", "Classement"),
    ("/live", "Live"),
    ("/replays", "Replay"),
    ("/admin", "Admin"),
)

# Both faces fall back to local ones (a narrow sans, the system mono), so a
# venue without internet gets a plainer page rather than a broken one.
_FONTS = (
    "https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@800"
    "&family=Martian+Mono:wght@400;700&display=swap"
)

_CSS = """
  :root { color-scheme: dark;
    --asphalt: #121310; --kerb: #1a1b17; --line: #2c2d27;
    --ink: #ece8dc; --muted: #8b887c; --bad: #ff6b4a; --ok: #7ccf6a;
    --gold: #d9ad3c; --silver: #b8bcc2; --bronze: #b87a4b;
    --display: "Big Shoulders Display", "Arial Narrow", sans-serif;
    --mono: "Martian Mono", ui-monospace, Menlo, Consolas, monospace; }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 0 16px 64px; color: var(--ink);
    font: 400 13px/1.7 var(--mono); background: var(--asphalt); }
  main { max-width: 860px; margin: 0 auto; padding-top: 28px; }
  h1, h2 { font-family: var(--display); font-weight: 800;
    text-transform: uppercase; line-height: 1; }
  h1 { font-size: 3.2rem; margin: 0 0 12px; letter-spacing: 0.01em; }
  h2 { font-size: 1.6rem; margin: 0 0 4px; letter-spacing: 0.02em; }
  section { margin: 44px 0 0; }
  .status { margin: 0; color: var(--muted); font-variant-numeric: tabular-nums; }
  .status strong { color: var(--ink); font-weight: 700; }
  .muted { color: var(--muted); }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 8px 10px;
    border-bottom: 1px solid var(--line); }
  th { font-size: 0.78em; font-weight: 400; color: var(--muted);
    border-bottom-color: var(--muted); }
  td:first-child { width: 3.5ch; font-family: var(--display); font-weight: 800;
    font-size: 1.35rem; line-height: 1; color: var(--muted); text-align: center; }
  tbody tr:nth-child(-n+3) td:first-child { color: var(--asphalt); }
  tbody tr:nth-child(1) td:first-child { background: var(--gold); }
  tbody tr:nth-child(2) td:first-child { background: var(--silver); }
  tbody tr:nth-child(3) td:first-child { background: var(--bronze); }
  tbody tr:first-child td { font-weight: 700; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  /* Beats the podium colours above, which would paint it the page colour. */
  td.empty { width: auto; font: inherit; text-align: left;
    color: var(--muted) !important;
    background: none !important; }
  .bad { color: var(--bad); }
  .live { color: var(--ink); font-weight: 700; white-space: nowrap; }
  .live::before { content: ""; display: inline-block; width: 7px; height: 7px;
    margin-right: 7px; vertical-align: 1px; background: var(--bad);
    animation: blink 1.2s steps(2, jump-none) infinite; }
  .admin { padding: 18px 20px; border: 1px dashed var(--muted); }
  .controls { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
  form.inline { display: inline; }
  button, input { font: inherit; padding: 8px 14px; border-radius: 0;
    border: 1px solid var(--line); background: var(--kerb); color: var(--ink); }
  button { cursor: pointer; }
  button:hover { border-color: var(--ink); }
  button.primary { background: var(--ink); border-color: var(--ink);
    color: var(--asphalt); font-weight: 700; }
  button.primary:hover { background: var(--muted); border-color: var(--muted); }
  button.danger { color: var(--bad); border-color: var(--bad); }
  input:focus, button:focus-visible { outline: 2px solid var(--ink);
    outline-offset: 2px; }
  details summary { cursor: pointer; color: var(--muted); }
  main.wide { max-width: 1200px; }
  a { color: var(--ink); }
  a:hover { color: var(--muted); }
  /* The header every tab shares: title and identity, the tabs, the status. */
  .top { padding-bottom: 0; border-bottom: 1px solid var(--line); }
  .top .bar { display: flex; flex-wrap: wrap; align-items: baseline;
    gap: 8px 24px; margin: 0 0 14px; }
  .top .bar .who { margin-left: auto; }
  .brand { font: 800 2rem/1 var(--display); text-transform: uppercase;
    letter-spacing: 0.01em; color: var(--ink); text-decoration: none; }
  .brand:hover { color: var(--ink); }
  .who { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px;
    margin: 0; color: var(--muted); }
  .who strong { color: var(--ink); }
  .who button { padding: 4px 10px; }
  /* The start kit in the header: a download icon, its name beside it on
     two small lines. */
  .who a.kit { display: flex; align-items: center; gap: 5px; padding: 2px;
    color: var(--ink); text-decoration: none; }
  .who a.kit span { font: 800 0.8rem/0.95 var(--display);
    text-transform: uppercase; letter-spacing: 0.04em; }
  .who a.kit:hover { color: var(--muted); }
  .who a.kit:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
  .tabs { display: flex; flex-wrap: wrap; gap: 0 28px; }
  .tabs a { padding: 6px 0 8px; border-bottom: 3px solid transparent;
    font: 800 1.2rem/1 var(--display); text-transform: uppercase;
    letter-spacing: 0.03em; color: var(--muted); text-decoration: none; }
  .tabs a:hover { color: var(--ink); }
  .tabs a[aria-current=page] { color: var(--ink); border-bottom-color: var(--ink); }
  .back { margin: 24px 0 0; }
  .play-race { margin: 0 0 12px; }
  /* Signing in and signing up: one narrow column, one form at a time. */
  main.narrow { max-width: 460px; padding-top: 12vh; }
  main.narrow h1 { font-size: 2.6rem; }
  main.narrow section { margin-top: 32px; }
  form.stack { display: grid; gap: 14px; }
  form.stack input:not([type=radio]) { width: 100%; }
  form.stack button { justify-self: start; }
  fieldset.role { display: flex; margin: 0; padding: 0; border: 0; }
  fieldset.role legend { padding: 0; margin: 0 0 6px; color: var(--muted); }
  fieldset.role label { flex: 1; position: relative; padding: 8px 14px;
    text-align: center; color: var(--muted); cursor: pointer;
    border: 1px solid var(--line); background: var(--kerb); }
  fieldset.role label + label { border-left: 0; }
  fieldset.role input { position: absolute; opacity: 0; inset: 0; margin: 0;
    cursor: pointer; }
  fieldset.role label:has(:checked) { color: var(--asphalt); font-weight: 700;
    background: var(--ink); border-color: var(--ink); }
  fieldset.role label:has(:focus-visible) { outline: 2px solid var(--ink);
    outline-offset: 2px; }
  /* The admin signs in with the password alone. */
  form.stack:has(input[value=admin]:checked) .team-only { display: none; }
  /* The start kit on the sign-in page: a sticker slapped somewhere on the
     page, gold, with a die-cut white rim and a drop shadow. `_STICKER_JS`
     picks where, and the side of the page it lands on picks which way it
     leans; it stays hidden until placed. */
  a.sticker { position: absolute; top: 0; left: 0; z-index: 1;
    visibility: hidden; --tilt: 14deg;
    display: grid; place-content: center; gap: 2px; width: 132px;
    height: 132px; padding: 12px; border-radius: 50%; text-align: center;
    color: var(--asphalt); background: var(--gold); text-decoration: none;
    font: 800 1.35rem/0.95 var(--display); text-transform: uppercase;
    letter-spacing: 0.02em; transform: rotate(var(--tilt));
    box-shadow: 0 0 0 5px var(--ink), 6px 10px 0 5px rgba(0, 0, 0, 0.55);
    transition: transform 0.25s cubic-bezier(0.3, 1.6, 0.5, 1); }
  a.sticker b { font: 700 1.9rem/1 var(--mono); }
  a.sticker:hover { color: var(--asphalt);
    transform: rotate(var(--tilt)) scale(1.06); }
  a.sticker:focus-visible { outline: 2px solid var(--ink); outline-offset: 8px; }
  @media (max-width: 820px) {
    a.sticker { width: 88px; height: 88px; padding: 8px;
      font-size: 0.95rem; box-shadow: 0 0 0 4px var(--ink),
        4px 6px 0 4px rgba(0, 0, 0, 0.55); }
    a.sticker b { font-size: 1.3rem; } }
  .aside { margin: 28px 0 0; padding: 14px 0 0; border-top: 1px solid var(--line);
    color: var(--muted); }
  /* The push tab's Push button: a file picker dressed as a button. The input
     is hidden from sight but not from focus, keyboards or form checks. */
  .push form { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
  /* The next version's description, typed before Push picks the file. */
  .push input.describe { flex: 0 1 26em; min-width: 0; }
  label.pick { position: relative; display: inline-block; padding: 8px 14px;
    color: var(--asphalt); font-weight: 700; cursor: pointer;
    background: var(--ink); border: 1px solid var(--ink); }
  label.pick:hover { background: var(--muted); border-color: var(--muted); }
  label.pick:has(:focus-visible) { outline: 2px solid var(--ink);
    outline-offset: 2px; }
  label.pick input { position: absolute; width: 1px; height: 1px; margin: 0;
    padding: 0; opacity: 0; overflow: hidden; }
  /* Pull: a link dressed as a quieter button, without an href until there
     is a controller to pull. */
  a.pull { display: inline-block; padding: 8px 14px; color: var(--ink);
    font-weight: 700; text-decoration: none; border: 1px solid var(--ink); }
  a.pull:hover { color: var(--asphalt); background: var(--ink); }
  a.pull:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
  a.pull[aria-disabled] { color: var(--muted); cursor: not-allowed;
    background: none; border-color: var(--line); }
  label.pick:has(:disabled) { color: var(--muted); cursor: not-allowed;
    background: var(--kerb); border-color: var(--line); }
  .pushed { margin: 28px 0 0; font: 800 1.6rem/1 var(--display);
    text-transform: uppercase; letter-spacing: 0.02em; }
  .pushed.ok { color: var(--ok); }
  .trace { color: var(--bad); white-space: pre-wrap; word-break: break-word; }
  /* The push tab's versions: the one racing next is marked, and each
     description is a small form of its own, input and button on one line.
     On a phone the table scrolls sideways rather than the page. */
  section.versions { overflow-x: auto; }
  .versions td { vertical-align: middle; white-space: nowrap; }
  /* Not a podium: undoes the top three rows' first-cell colours. */
  .versions tbody tr td:first-child { color: var(--ink); }
  .versions td:nth-child(4) { width: 100%; }
  .versions .next { margin-left: 8px; color: var(--ok); font-size: 0.85em; }
  form.described { display: flex; gap: 6px; }
  form.described input { flex: 1 1 auto; min-width: 8em; padding: 4px 8px; }
  form.described button { padding: 4px 10px; }
  label { color: var(--muted); }
  label input { display: block; margin-top: 4px; }
  .notice { margin: 0 0 16px; padding: 12px 16px; border-left: 3px solid var(--ink);
    background: var(--kerb); }
  .notice.bad { border-left-color: var(--bad); }
  .notice p { margin: 0; }
  tr.me td { background: var(--kerb); }
  tr.me td:nth-child(2)::after { content: "  ← toi"; color: var(--muted);
    font-weight: 400; }
  /* The race lists and a race's result are logs, not podiums. */
  table.log td:first-child { width: auto; font: inherit; color: inherit;
    text-align: left; background: none !important; }
  table.log tbody tr:first-child td { font-weight: 400; }
  .err { display: block; color: var(--bad); font-size: 0.85em;
    white-space: pre-wrap; word-break: break-word; }
  pre { margin: 8px 0 0; padding: 10px 12px; overflow: auto; max-height: 30em;
    font: 400 12px/1.5 var(--mono); background: var(--kerb);
    border: 1px solid var(--line); }
  #player, #live { margin: 20px 0 0; }
  /* The version the team's own car raced, under the race's player. */
  p.version { margin: 12px 0 0; }
  /* The live tab of a server with no live view. */
  .idle { display: grid; place-items: center; aspect-ratio: 16 / 9;
    margin: 20px 0 0; padding: 16px; border: 1px solid var(--line);
    text-align: center; color: var(--muted); font: 800 2rem/1 var(--display);
    text-transform: uppercase; letter-spacing: 0.03em; }
  /* The live tab fits the window, with no page scroll: the header on top
     and the player, or the idle notice, taking the height that is left. */
  @media (min-width: 701px) and (min-height: 500px) {
    body:has(#live, .idle) { height: 100vh; height: 100dvh; display: flex;
      flex-direction: column; padding-bottom: 16px; }
    main:has(#live, .idle), main:has(#live, .idle) > .tab { flex: 1 1 auto;
      min-height: 0; width: 100%; display: flex; flex-direction: column; }
    .tab > #live, .tab > .idle { flex: 1 1 auto; min-height: 0;
      aspect-ratio: auto; } }
  @keyframes blink { 50% { opacity: 0.15; } }
  @keyframes enter { from { opacity: 0; transform: translateX(-8px); } }
  /* Only the page's own blocks enter: the parts a refresh swaps in sit
     one level down, so they never replay it. */
  main > * { animation: enter 0.45s cubic-bezier(0.2, 0.7, 0.2, 1) both; }
  main > :nth-child(2) { animation-delay: 60ms; }
  main > :nth-child(3) { animation-delay: 120ms; }
  main > :nth-child(n+4) { animation-delay: 180ms; }
  @media (prefers-reduced-motion: reduce) {
    *, *::before { animation: none !important; } }
"""


@dataclass(frozen=True)
class Viewer:
    """Who a signed-in page is for: a team, or the admin when `team` is None."""

    team: Team | None = None

    @property
    def admin(self) -> bool:
        return self.team is None

    @property
    def tabs(self) -> tuple[tuple[str, str], ...]:
        return ADMIN_TABS if self.admin else TEAM_TABS

    @property
    def me(self) -> str | None:
        """The team's name, for picking out its own lines."""
        return None if self.team is None else self.team.name


_DOWNLOAD_ICON = (
    '<svg viewBox="0 0 24 24" width="20" height="20" fill="none"'
    ' stroke="currentColor" stroke-width="2" stroke-linecap="round"'
    ' stroke-linejoin="round" aria-hidden="true">'
    '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>'
)


def login_page(role: str = "team", error: str | None = None, name: str = "") -> str:
    """`/` signed out: one sign-in form, as a team or as the admin.

    After a refused attempt, `error` says why, and the form comes back with
    the `role` and the team `name` that were sent.
    """
    admin = role == "admin"
    team_value = f" value='{html.escape(name)}'" if name and not admin else ""
    body = f"""
    <h1>{EVENT_TITLE}</h1>
    <section>
      <h2>Connexion</h2>
      {_notice(error)}
      <form method="post" action="/login" class="stack">
        <fieldset class="role"><legend>Se connecter en tant que</legend>
          <label><input type="radio" name="role" value="team"
            {"" if admin else "checked"}>Équipe</label>
          <label><input type="radio" name="role" value="admin"
            {"checked" if admin else ""}>Admin</label>
        </fieldset>
        <label class="team-only">Nom d'équipe
          <input name="team" maxlength="32" autocomplete="username"{team_value}
            {"" if name and not admin else "autofocus"}></label>
        <label>Mot de passe
          <input type="password" name="password" autocomplete="current-password"
            required{" autofocus" if name or admin else ""}></label>
        <button class="primary">Se connecter</button>
      </form>
      <p class="aside">Pas encore d'équipe ?
        <a href="/signup">Créer une équipe</a></p>
    </section>
    <a class="sticker" href="/start-kit.zip" download>
      <span>Kit de départ</span><b aria-hidden="true">↓</b></a>
    """
    return shell("Connexion", body, script=_STICKER_JS, narrow=True)


# Slaps the sign-in page's start kit sticker at a random spot, on
# every load and again on every click. The spot keeps the sticker whole on
# screen, clear of the edges, and off the column the form is in; where the
# column fills the width, as on a phone, the spot can be below it. The angle
# is fixed, mirrored by side: leaning out toward the edge it sits nearest.
_STICKER_JS = """(() => {
  const kit = document.querySelector("a.sticker");
  const parts = [...document.querySelector("main").children]
    .filter((el) => el !== kit);
  const place = () => {
    const size = kit.offsetWidth, edge = 24, gap = 28;
    const rects = parts.map((el) => el.getBoundingClientRect());
    const top = Math.min(...rects.map((r) => r.top)) + scrollY - gap;
    const bottom = Math.max(...rects.map((r) => r.bottom)) + scrollY + gap;
    const left = Math.min(...rects.map((r) => r.left)) - gap;
    const right = Math.max(...rects.map((r) => r.right)) + gap;
    const width = document.documentElement.clientWidth;
    const height = Math.max(innerHeight, bottom + size + edge);
    const clear = (x, y) => x + size < left || x > right
      || y + size < top || y > bottom;
    let x, y;
    for (let i = 0; i < 500; i++) {
      x = edge + Math.random() * (width - size - 2 * edge);
      y = edge + Math.random() * (height - size - 2 * edge);
      if (clear(x, y)) break;
    }
    if (!clear(x, y)) y = bottom;
    kit.style.left = `${x}px`;
    kit.style.top = `${y}px`;
    kit.style.setProperty("--tilt", x + size / 2 < width / 2 ? "-14deg" : "14deg");
    kit.style.visibility = "visible";
  };
  place();
  kit.addEventListener("click", () => setTimeout(place, 0));
  addEventListener("resize", place);
})();"""


def signup_page(error: str | None = None, name: str = "") -> str:
    """`/signup`: create a team, with the refused `name` handed back."""
    value = f" value='{html.escape(name)}'" if name else ""
    body = f"""
    <h1>{EVENT_TITLE}</h1>
    <section>
      <h2>Créer une équipe</h2>
      {_notice(error)}
      <form method="post" action="/signup" class="stack">
        <label>Nom d'équipe
          <input name="team" maxlength="32" autocomplete="username" required
            autofocus{value}></label>
        <label>Mot de passe
          <input type="password" name="password" autocomplete="new-password"
            required></label>
        <button class="primary">Créer l'équipe</button>
      </form>
      <p class="aside">Déjà une équipe ? <a href="/">Se connecter</a></p>
    </section>
    """
    return shell("Créer une équipe", body, narrow=True)


def _notice(error: str | None) -> str:
    if not error:
        return ""
    return f"<div class='notice bad'><p>{html.escape(error)}</p></div>"


def leaderboard_page(store: Store, scheduler: Scheduler, viewer: Viewer) -> str:
    """The leaderboard tab: both boards, the viewer's own line picked out."""
    return tab_page(scheduler, viewer, "/leaderboard", boards(store, viewer.me))


def admin_page(store: Store, scheduler: Scheduler) -> str:
    """The admin tab, in English: the qualification cadence, an official
    race, the sign-up lock, deletion."""
    return tab_page(
        scheduler,
        Viewer(),
        "/admin",
        _admin_controls(store, scheduler),
        english=True,
    )


def live_page(
    scheduler: Scheduler,
    viewer: Viewer | None = None,
    host: str = "127.0.0.1",
    proxy: str | None = None,
) -> str:
    """The live tab, for every viewer: the race drawn by the replay player,
    fed by the race worker's live view.

    That view is the race subprocess's WebSocket, on the scheduler's port
    of the machine serving this page, so `host` is the host name the
    browser reached this page by. Behind the deploy's reverse proxy,
    `proxy` is the origin the browser reached it on instead (see
    `live_url`). The player waits between races and
    reconnects by itself for the next. A headless server has no live view,
    and the tab says so.

    A team's own car starts circled on the track; the admin's view, the
    projector's, starts with none.

    The admin alone also gets the Play button while a launched official
    race waits on the grid for it (English, like everything only the admin
    sees); everyone else sees the waiting grid.
    """
    viewer = viewer or Viewer()
    if not scheduler.live:
        body = "<p class='idle'>Pas de course en direct sur ce serveur</p>"
        return tab_page(scheduler, viewer, "/live", body)
    url = live_url(host, scheduler.port, proxy)
    play = ""
    if viewer.admin:
        button = (
            "<form method='post' action='/admin/play' class='play-race'>"
            "<button class='primary'>&#9654; Play</button></form>"
            if scheduler.awaiting_play
            else ""
        )
        play = f"<div id='play' data-live>{button}</div>"
    options = ""
    if viewer.me is not None:
        options = ", " + json.dumps({"me": viewer.me}).replace("</", "<\\/")
    body = (
        f"{play}<div id='live' data-ws='{html.escape(url)}'></div><script>"
        "(() => { const el = document.getElementById('live');"
        f" ReplayPlayer.live(el, el.dataset.ws{options}); }})();</script>"
    )
    return tab_page(scheduler, viewer, "/live", body, wide=True, replay=True)


def live_url(host: str, port: int, proxy: str | None = None) -> str:
    """The race worker's live WebSocket, on `host`; an IPv6 host is
    bracketed, as a URL needs.

    Behind the deploy's reverse proxy the browser cannot reach `port`, so
    the proxy forwards `LIVE_PROXY_PATH` on its own origin to the worker:
    `proxy` is that origin ("https://name"), and the socket is `wss://`
    when the page is `https://`.
    """
    if proxy is not None:
        scheme, _, origin = proxy.partition("://")
        return f"{'wss' if scheme == 'https' else 'ws'}://{origin}{LIVE_PROXY_PATH}"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"ws://{host}:{port}/ws"


def tab_page(
    scheduler: Scheduler,
    viewer: Viewer,
    active: str,
    body: str,
    title: str | None = None,
    wide: bool = False,
    replay: bool = False,
    english: bool = False,
) -> str:
    """One tab under the shared header. `active` is the tab's path; the
    page title defaults to that tab's label. `english` switches the header's
    words, and the page's language, to English; the tab labels and the
    event's status stay as they are."""
    lang = "en" if english else "fr"
    words = _HEADER_TEXT[lang]
    poll = LIVE_STATUS_POLL_SECONDS if active == "/live" else STATUS_POLL_SECONDS
    labels = dict(viewer.tabs)
    current = " aria-current='page'"
    tabs = "".join(
        f"<a href='{path}'{current if path == active else ''}>{label}</a>"
        for path, label in viewer.tabs
    )
    who = (
        "<span><strong>Admin</strong></span>"
        if viewer.team is None
        else f"<span>Équipe <strong>{html.escape(viewer.team.name)}</strong></span>"
    )
    header = f"""
    <header class="top">
      <div class="bar">
        <a class="brand" href="/">{EVENT_TITLE}</a>
        {status_bar(scheduler, poll)}
        <div class="who">{who}
          <a class="kit" href="/start-kit.zip" download
            aria-label="{words["kit"]}">{_DOWNLOAD_ICON}<span
            aria-hidden="true">{words["kit_lines"]}</span></a>
          <form method="post" action="/logout" class="inline">
          <button>{words["sign_out"]}</button></form></div>
      </div>
      <nav class="tabs" aria-label="{words["tabs"]}">{tabs}</nav>
    </header>
    """
    return shell(
        title or labels.get(active, EVENT_TITLE),
        f"{header}<div class='tab'>{body}</div>",
        script=_SWAP_SCRIPT + _STATUS_SCRIPT,
        wide=wide,
        replay=replay,
        lang=lang,
    )


def boards(store: Store, me: str | None = None) -> str:
    """Both leaderboards, always, the official one first because it decides
    the challenge; it says when it is empty."""
    standings = store.standings()
    return (
        f"<div id='boards' data-live>{_official_table(standings, me)}"
        f"{_qualification_table(standings, me)}</div>"
    )


def status_bar(scheduler: Scheduler, poll: float = STATUS_POLL_SECONDS) -> str:
    """The event's one status, the same for every viewer: the race running,
    or the paused cadence, or the countdown to the next qualification. It
    is said in French on every tab, Admin included.

    The data attributes seed `_STATUS_SCRIPT`, which keeps the line up to
    date from then on: it ticks the countdown and polls `/api/standings`
    every `poll` seconds.
    """
    running = scheduler.running_race
    remaining = scheduler.seconds_to_next_race
    paused = scheduler.paused
    attrs = (
        f" data-running='{running or ''}' data-paused='{int(paused)}'"
        f" data-next='{'' if remaining is None else f'{remaining:.1f}'}'"
        f" data-poll='{poll:g}'"
    )
    words = _STATUS_TEXT
    if running:
        text = f"<span class='live'>{words[running]}</span>"
    elif paused:
        text = words["paused"]
    elif remaining is not None:
        text = f"{words['next']}<strong>{countdown(remaining)}</strong>"
    else:
        text = ""
    return (
        f"<p class='status' id='status' lang='fr' aria-live='polite'{attrs}>{text}</p>"
    )


def countdown(seconds: float) -> str:
    """Minutes and seconds, as "3:12"; the script says it the same way."""
    whole = max(0, int(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def count(n: int, one: str, many: str) -> str:
    """`n` and its noun; French keeps the singular for zero and one."""
    return f"<strong>{n}</strong> {one if n <= 1 else many}"


def _admin_controls(store: Store, scheduler: Scheduler) -> str:
    """The admin's buttons: the cadence, an official race, the sign-up lock,
    deletion."""
    cadence = (
        "<form method='post' action='/admin/resume'>"
        "<button>Resume qualification</button></form>"
        if scheduler.paused
        else "<form method='post' action='/admin/pause'>"
        "<button>Pause qualification</button></form>"
    )
    signups = (
        "<form method='post' action='/admin/unlock-signups'>"
        "<button>Unlock team creation</button></form>"
        if store.signups_locked
        else "<form method='post' action='/admin/lock-signups'>"
        "<button>Lock team creation</button></form>"
    )
    rows = "".join(
        f"<form method='post' action='/admin/delete-team' class='inline'>"
        f"<input type='hidden' name='team' value='{html.escape(t.name)}'>"
        f"<button class='danger'>Delete {html.escape(t.name)}</button></form>"
        for t in store.accounts()
    )
    return f"""
    <section class="admin">
      <div class="controls">
        {cadence}
        <form method="post" action="/admin/official-race">
          <button class="primary">Start an official race</button>
        </form>
        {signups}
      </div>
      <details><summary>Delete a team</summary>
      <div class="controls">{rows or "<p class='muted'>No teams.</p>"}</div>
      </details>
      {_persistence(store)}
    </section>
    """


def _persistence(store: Store) -> str:
    """Whether the event survives a restart, and when it was last saved."""
    if store.state_file is None:
        return "<p class='notice bad'>In memory only: a restart erases the event.</p>"
    lines = []
    if store.save_error is not None:
        lines.append(
            f"<p class='notice bad'>Last save failed: "
            f"{html.escape(store.save_error)}</p>"
        )
    if store.saved_at is not None:
        lines.append(
            f"<p class='muted'>State saved at "
            f"{time.strftime('%H:%M:%S', time.localtime(store.saved_at))}.</p>"
        )
    if store.restored_at is not None:
        lines.append(
            f"<p class='muted'>Restored from disk at "
            f"{time.strftime('%H:%M', time.localtime(store.restored_at))}.</p>"
        )
    return "".join(lines)


def _qualification_table(standings: list[Standing], me: str | None) -> str:
    """The qualification board: points over the last qualifications, most
    first."""
    rows = "".join(
        f"<tr{_mine(s, me)}><td>{i}</td><td>{html.escape(s.team)}</td>"
        f"<td class='num'>{s.qualification_score}</td></tr>"
        for i, s in enumerate(by_qualification_score(standings), 1)
    )
    return f"""
    <section>
      <h2>Classement Qualif</h2>
      <p class='muted'>Un point par dépassement, sur les {QUALIFICATION_WINDOW}
      dernières qualifs.</p>
      <table><thead><tr><th>#</th><th>Équipe</th><th class="num">Points</th></tr>
      </thead><tbody>{rows or empty_row(3)}</tbody></table>
    </section>
    """


def _official_table(standings: list[Standing], me: str | None) -> str:
    """The official board: every official result, and their sum, lowest first.

    A race the team missed reads "—" in its results; its points count it
    as last-plus-one there. Teams with no official race are left off entirely
    rather than shown blank — they are on the qualification board below.
    Before the first official race the board is there, and says so.
    """
    rows = "".join(
        f"<tr{_mine(s, me)}><td>{i}</td><td>{html.escape(s.team)}</td>"
        f"<td class='num'>{_results(s.official_positions)}</td>"
        f"<td class='num'>{s.official_score}</td></tr>"
        for i, s in enumerate((s for s in standings if s.official_score is not None), 1)
    )
    empty = (
        "<tr><td colspan='4' class='empty'>Aucune course officielle pour "
        "l'instant.</td></tr>"
    )
    return f"""
    <section>
      <h2>Classement courses officielles</h2>
      <table><thead><tr><th>#</th><th>Équipe</th><th class="num">Résultats</th>
      <th class="num">Points</th></tr></thead><tbody>{rows or empty}</tbody></table>
    </section>
    """


def _results(positions: Sequence[int | None]) -> str:
    """One team's official finishing positions, in race order: "2, —, 1"."""
    return ", ".join("—" if p is None else str(p) for p in positions)


def _mine(standing: Standing, me: str | None) -> str:
    """The class that picks out the signed-in team's own line."""
    return " class='me'" if standing.team == me else ""


def empty_row(columns: int) -> str:
    return f"<tr><td colspan='{columns}' class='empty'>Rien pour l'instant.</td></tr>"


# Every tab's refresh: fetch the page again and swap in the parts marked
# `data-live`, leaving everything else (a form, a picked file, the player)
# alone. A fetch that lands on the sign-in page means the session is gone (a
# restart signs everyone out), so the tab goes there too — unless a file is
# picked, which a reload would lose.
_SWAP_SCRIPT = f"""
setInterval(async () => {{
  try {{
    const res = await fetch(location.pathname, {{ credentials: "same-origin" }});
    if (res.redirected) {{
      const picked = [...document.querySelectorAll("input[type=file]")]
        .some(f => f.files && f.files.length);
      if (!picked) location.replace("/");
      return;
    }}
    if (!res.ok) return;
    const fresh = new DOMParser().parseFromString(await res.text(), "text/html");
    for (const el of document.querySelectorAll("[data-live]")) {{
      const next = fresh.getElementById(el.id);
      if (next && next.hasAttribute("data-live")) el.replaceWith(next);
    }}
  }} catch (e) {{}}
}}, {REFRESH_SECONDS * 1000});
"""

# The header's status. It says what `status_bar` says, and keeps saying it:
# the countdown ticks every second, `/api/standings` is asked for the
# event's state every `data-poll` seconds, and again every second once the
# countdown has run out, so the race that starts then shows up at once.
# Its words are `_STATUS_TEXT`'s, French on every tab. Each poll's answer
# also goes out to the page as a `cocoracer:status` event on `document`,
# whose `detail.running` is the race running or null: the Live tab's
# player connects to the race's socket on it (`static/replay.js`).
_STATUS_SCRIPT = f"""
(() => {{
  const el = document.getElementById("status");
  if (!el) return;
  const words = {json.dumps(_STATUS_TEXT, ensure_ascii=False)};
  let state = {{
    running: el.dataset.running || null,
    paused: el.dataset.paused === "1",
    deadline: el.dataset.next === "" ? null
      : Date.now() + parseFloat(el.dataset.next) * 1000,
  }};
  const every = (parseFloat(el.dataset.poll) || {STATUS_POLL_SECONDS}) * 1000;
  let polled = Date.now();
  const clock = (s) => `${{Math.floor(s / 60)}}:${{String(s % 60).padStart(2, "0")}}`;
  const left = () => Math.max(0, Math.floor((state.deadline - Date.now()) / 1000));
  function render() {{
    el.replaceChildren();
    if (state.running) {{
      const dot = document.createElement("span");
      dot.className = "live";
      dot.textContent = words[state.running] || state.running;
      el.append(dot);
    }} else if (state.paused) {{
      el.textContent = words.paused;
    }} else if (state.deadline !== null) {{
      const n = document.createElement("strong");
      n.textContent = clock(left());
      el.append(words.next, n);
    }}
  }}
  async function poll() {{
    polled = Date.now();
    try {{
      const res = await fetch("/api/standings",
        {{ credentials: "same-origin", cache: "no-store" }});
      if (!res.ok) return;
      const s = await res.json();
      state = {{
        running: s.running,
        paused: s.paused,
        deadline: s.next_race_in === null ? null
          : Date.now() + s.next_race_in * 1000,
      }};
      render();
      document.dispatchEvent(new CustomEvent("cocoracer:status",
        {{ detail: {{ running: state.running }} }}));
    }} catch (e) {{}}
  }}
  setInterval(() => {{
    const due = !state.running && !state.paused && state.deadline !== null
      && left() === 0;
    // A little short of `every`, so that the 1 s timer's jitter does not
    // skip a poll on the Live tab, where `every` is the timer's own period.
    if (due || Date.now() - polled >= every - 250) poll();
    else render();
  }}, 1000);
}})();
"""


def shell(
    title: str,
    body: str,
    script: str = "",
    wide: bool = False,
    narrow: bool = False,
    replay: bool = False,
    lang: str = "fr",
) -> str:
    """The page frame. `wide` widens the column for the replay player,
    whose side panel needs the room, and `narrow` shrinks it for a lone
    form; `replay` loads the player itself; `lang` is the page's language."""
    head = ""
    if replay:
        head += (
            '<link rel="stylesheet" href="/static/replay.css">'
            '<script src="/static/replay.js"></script>'
        )
    tail = f"<script>{script}</script>" if script else ""
    width = " class='wide'" if wide else " class='narrow'" if narrow else ""
    return f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{head}<title>{html.escape(title)} — {EVENT_TITLE}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="{_FONTS}">
<style>{_CSS}</style></head><body>
<main{width}>
{body}
</main>{tail}</body></html>"""
