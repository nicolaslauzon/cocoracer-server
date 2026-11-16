"""The HTTP surface: signing in, the tabs, pushing, replays, the
leaderboards, and the admin controls."""

import io
import json
import re
import threading
import time
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from cocoracer_server.app import (
    ADMIN_COOKIE,
    REPO_START_KIT,
    SERVER_PLACEHOLDER,
    TEAM_COOKIE,
    create_app,
)
from cocoracer_server.runner import RaceOutcome
from cocoracer_server.scheduler import Scheduler
from cocoracer_server.store import (
    MAX_DESCRIPTION_CHARS,
    MAX_SOURCE_BYTES,
    Store,
    Team,
    TeamKey,
)
from tests.conftest import CAR, RecordingRunner, enter, key

PASSWORD = "let-me-in"
REPLAY_BYTES = (Path(__file__).parent / "data" / "replay_fixture.json").read_bytes()
REPLAY = json.loads(REPLAY_BYTES)
WARNED = "import importlib\n" + CAR
BROKEN = "import requests\nx = 1\n"
ALPHA_LOG = {"lines": [[0.0, "loaded"], [3.5, "speed 4.2"]], "truncated": False}
BETA_LOG = {"lines": [[3.5, "beta's secret"]], "truncated": True}
NO_LOG = {"lines": [], "truncated": False}


def _eventually(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Wait for a background race to have had its effect."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


@pytest.fixture
def app(store: Store, scheduler: Scheduler) -> FastAPI:
    return create_app(store=store, scheduler=scheduler, admin_password=PASSWORD)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    assert _admin_login(client, PASSWORD).status_code == 303
    return client


@pytest.fixture
def team(client: TestClient) -> TestClient:
    """A client signed in as a freshly signed-up team "alpha", password "pw"."""
    assert _signup(client, "alpha", "pw").status_code == 303
    return client


def _signup(client: TestClient, team: str, password: str) -> Response:
    response: Response = client.post(
        "/signup",
        data={"team": team, "password": password},
        follow_redirects=False,
    )
    return response


def _login(client: TestClient, team: str, password: str) -> Response:
    response: Response = client.post(
        "/login",
        data={"team": team, "password": password},
        follow_redirects=False,
    )
    return response


def _admin_login(client: TestClient, password: str) -> Response:
    response: Response = client.post(
        "/login",
        data={"role": "admin", "team": "", "password": password},
        follow_redirects=False,
    )
    return response


def _push(
    client: TestClient, source: str | bytes | None, description: str | None = None
) -> Response:
    """Push through the page's form: `source` as the picked file, or, for
    None, what a browser sends when no file is picked, an empty part, and
    `description` as typed, if any."""
    files = {"file": ("", b"", "application/octet-stream")}
    if source is not None:
        raw = source.encode() if isinstance(source, str) else source
        files = {"file": ("my_car.py", raw, "text/x-python")}
    data = {} if description is None else {"description": description}
    response: Response = client.post(
        "/push", data=data, files=files, follow_redirects=False
    )
    return response


def _description_input(body: str) -> str:
    """The push form's description input, as the page writes it."""
    start = body.rindex("<input", 0, body.index('name="description"'))
    return body[start : body.index(">", start) + 1]


def _signed_out(client: TestClient) -> bool:
    """Whether `client` is shown the sign-in page rather than a tab."""
    response = client.get("/", follow_redirects=False)
    return response.status_code == 200 and 'action="/login"' in response.text


class TestAccounts:
    def test_signing_up_signs_the_team_in(self, client: TestClient) -> None:
        response = _signup(client, "alpha", "pw")
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        landing = client.get("/", follow_redirects=False)
        assert landing.headers["location"] == "/leaderboard"
        body = client.get("/push").text
        assert 'type="file" name="file"' in body
        assert "id='result'" not in body

    def test_the_session_cookie_is_not_the_password_and_is_httponly(
        self, client: TestClient
    ) -> None:
        response = _signup(client, "alpha", "hunter22")
        cookie = response.headers["set-cookie"]
        assert TEAM_COOKIE in cookie and "hunter22" not in cookie
        assert "httponly" in cookie.lower() and "samesite=lax" in cookie.lower()
        assert "secure" not in cookie.lower()

    def test_over_https_the_session_cookies_are_secure(self, app: FastAPI) -> None:
        with TestClient(app, base_url="https://cocoracer.example.org") as browser:
            team = _signup(browser, "alpha", "pw").headers["set-cookie"]
            admin = browser.post(
                "/login",
                data={"role": "admin", "password": PASSWORD},
                follow_redirects=False,
            ).headers.get_list("set-cookie")
        assert "secure" in team.lower()
        assert any(ADMIN_COOKIE in c and "secure" in c.lower() for c in admin)

    def test_a_forged_cookie_is_signed_out(self, client: TestClient) -> None:
        _signup(client, "alpha", "pw")
        client.cookies.set(TEAM_COOKIE, "alpha")
        assert _signed_out(client)

    def test_a_taken_name_is_refused_with_the_form_filled_back(
        self, team: TestClient, app: FastAPI
    ) -> None:
        with TestClient(app) as other:
            response = _signup(other, "alpha", "mine")
        assert response.status_code == 409
        assert "est déjà pris" in response.text
        assert "value='alpha'" in response.text

    def test_a_bad_name_is_refused_in_french(self, client: TestClient) -> None:
        response = _signup(client, "<script>alert(1)</script>", "pw")
        assert response.status_code == 400
        assert "ne peut contenir que" in response.text
        assert "<script>alert" not in response.text

    def test_the_seventeenth_team_is_told_the_event_is_full(
        self, scheduler: Scheduler
    ) -> None:
        app = create_app(
            store=Store(max_teams=1), scheduler=scheduler, admin_password=PASSWORD
        )
        with TestClient(app) as first, TestClient(app) as second:
            _signup(first, "alpha", "pw")
            response = _signup(second, "bravo", "pw")
        assert response.status_code == 409
        assert "est complet : 1 équipes" in response.text

    def test_signing_in_with_the_right_password(
        self, team: TestClient, app: FastAPI
    ) -> None:
        with TestClient(app) as laptop:
            response = _login(laptop, "alpha", "pw")
            assert response.status_code == 303
            assert laptop.get("/").url.path == "/leaderboard"
            assert 'type="file" name="file"' in laptop.get("/push").text

    def test_a_wrong_password_does_not_sign_in(
        self, team: TestClient, app: FastAPI
    ) -> None:
        with TestClient(app) as laptop:
            response = _login(laptop, "alpha", "nope")
            assert response.status_code == 401
            assert "ou mot de passe incorrect." in response.text
            assert TEAM_COOKIE not in response.cookies
            assert "value='alpha'" in response.text
            assert _signed_out(laptop)

    def test_signing_out(self, team: TestClient) -> None:
        response = team.post("/logout", follow_redirects=False)
        assert response.status_code == 303
        assert _signed_out(team)

    def test_a_deleted_team_is_signed_out_even_if_its_name_is_claimed_again(
        self, team: TestClient, app: FastAPI, store: Store
    ) -> None:
        store.delete("alpha")
        store.register("alpha", "someone else")
        assert _signed_out(team)

    @pytest.fixture
    def deleted_mid_request(
        self, team: TestClient, store: Store, monkeypatch: pytest.MonkeyPatch
    ) -> TestClient:
        """alpha, with a version, whose next request finds it signed in and
        then loses it: the admin deletes it and someone else takes the name
        between the session's check and the action."""
        _push(team, CAR)
        looked_up = store.team

        def then_deleted(team_key: TeamKey) -> Team | None:
            monkeypatch.setattr(store, "team", looked_up)
            found = looked_up(team_key)
            store.delete("alpha")
            store.register("alpha", "someone else")
            return found

        monkeypatch.setattr(store, "team", then_deleted)
        return team

    @pytest.mark.parametrize(
        "request_",
        [
            lambda c: _push(c, CAR, "late"),
            lambda c: c.post(
                "/versions/1/description",
                data={"description": "late"},
                follow_redirects=False,
            ),
            lambda c: c.get("/pull", follow_redirects=False),
            lambda c: c.get("/pull?version=1", follow_redirects=False),
        ],
        ids=["push", "describe", "pull", "pull-version"],
    )
    def test_an_action_that_loses_its_team_mid_request_signs_out(
        self,
        deleted_mid_request: TestClient,
        store: Store,
        request_: Callable[[TestClient], Response],
    ) -> None:
        response = request_(deleted_mid_request)
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        new = store.account("alpha")
        assert new is not None
        assert new.versions == () and not new.submitted
        assert store.feedback(new.key) is None

    def test_a_log_read_that_loses_its_team_mid_request_reads_nothing(
        self,
        deleted_mid_request: TestClient,
        store: Store,
        runner: RecordingRunner,
        scheduler: Scheduler,
    ) -> None:
        """The API is not redirected: a team gone between the check and the
        read gets an empty log, never the old team's."""
        runner.outcome = RaceOutcome(
            classified=("alpha",),
            statuses={"alpha": "finished"},
            replay=REPLAY_BYTES,
            logs={"alpha": ALPHA_LOG},
        )
        scheduler.run_qualification()
        assert store.log(1, key(store, "alpha")) == ALPHA_LOG
        response = deleted_mid_request.get("/api/races/1/logs")
        assert response.status_code == 200
        assert response.json() == NO_LOG


class TestPush:
    def test_a_file_is_pushed(self, team: TestClient, store: Store) -> None:
        response = _push(team, CAR)
        assert response.status_code == 303
        assert response.headers["location"] == "/push"
        assert store.teams()[0].source == CAR
        body = team.get("/push").text
        assert ("<p class='pushed ok' id='result'>OK</p>") in body
        assert "Push failed" not in body

    def test_the_form_is_one_button_that_pushes_on_pick(self, team: TestClient) -> None:
        body = team.get("/push").text
        form = body[body.index('<form method="post" action="/push"') :]
        form = form[: form.index("</form>")]
        assert 'type="file" name="file" accept=".py"' in form
        assert "Push</label>" in form
        assert ">Pull</a>" in form
        assert "this.form.submit()" in form
        # Without JavaScript, the only other button is the fallback.
        assert form.count("<button") == 1
        assert "<noscript><button" in form
        assert "<textarea" not in body and 'name="source"' not in body

    def test_each_push_is_a_version_with_its_description(
        self,
        team: TestClient,
        store: Store,
        runner: RecordingRunner,
        scheduler: Scheduler,
    ) -> None:
        body = team.get("/push").text
        assert 'placeholder="Description de v1 (optionnel)"' in body
        _push(team, CAR, "baseline")
        _push(team, WARNED)
        _push(team, CAR + "# v3\n", "late braking")
        (pushed,) = store.teams()
        assert [(v.label, v.description) for v in pushed.versions] == [
            ("v1", "baseline"),
            ("v2", ""),
            ("v3", "late braking"),
        ]
        assert 'placeholder="Description de v4 (optionnel)"' in team.get("/push").text
        scheduler.run_qualification()
        assert runner.calls[-1]["sources"] == {"alpha": CAR + "# v3\n"}

    def test_the_description_sits_before_push_in_the_form(
        self, team: TestClient
    ) -> None:
        body = team.get("/push").text
        form = body[body.index('<form method="post" action="/push"') :]
        form = form[: form.index("</form>")]
        assert 'name="description"' in form
        assert form.index('name="description"') < form.index('type="file"')

    def test_a_refused_push_keeps_the_typed_description(
        self, team: TestClient, store: Store
    ) -> None:
        _push(team, CAR)
        _push(team, BROKEN, "tighter <lines>")
        assert store.teams()[0].pushes == 1
        body = team.get("/push").text
        assert 'value="tighter &lt;lines&gt;"' in body
        assert 'placeholder="Description de v2 (optionnel)"' in body
        _push(team, b"\xff\xfe\x00binary", "still typed")
        assert 'value="still typed"' in team.get("/push").text
        _push(team, WARNED)
        assert "value=" not in _description_input(team.get("/push").text)
        assert [v.number for v in store.teams()[0].versions] == [1, 2]

    def test_an_overlong_description_is_refused(
        self, team: TestClient, store: Store
    ) -> None:
        _push(team, CAR, "x" * (MAX_DESCRIPTION_CHARS + 1))
        assert store.teams() == []
        assert "La description dépasse 200 caractères." in team.get("/push").text

    def test_pasted_code_is_not_a_push(self, team: TestClient, store: Store) -> None:
        response = team.post(
            "/push",
            data={"source": CAR},
            files={"file": ("", b"", "application/octet-stream")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert store.teams() == []
        assert "Le contrôleur est vide." in team.get("/push").text

    def test_the_pushed_code_is_not_shown(self, team: TestClient) -> None:
        _push(team, CAR)
        body = team.get("/push").text
        assert "class Car(Controller)" not in body
        assert "Voir le code envoyé" not in body and "<details" not in body

    def test_a_push_with_errors_is_refused_and_the_previous_one_kept(
        self, team: TestClient, store: Store
    ) -> None:
        _push(team, CAR)
        assert _push(team, BROKEN).status_code == 303
        assert store.teams()[0].source == CAR
        body = team.get("/push").text
        assert "<p class='pushed bad'>Push failed</p>" in body
        trace = body[body.index("<pre class='trace'>") :]
        trace = trace[: trace.index("</pre>")]
        assert "Envoi refusé, ta soumission précédente continue de courir" in trace
        assert "ligne 1 : " in trace and "requests" in trace
        assert "import requests\nx = 1" not in body

    def test_a_push_with_only_warnings_goes_through_quietly(
        self, team: TestClient, store: Store
    ) -> None:
        _push(team, WARNED)
        assert store.teams()[0].source == WARNED
        body = team.get("/push").text
        assert "id='result'>OK</p>" in body
        assert "importlib" not in body and "avertissement" not in body

    def test_the_pushed_controller_is_pulled_back_as_my_car(
        self, team: TestClient
    ) -> None:
        body = team.get("/push").text
        assert '<a class="pull" aria-disabled="true">Pull</a>' in body
        assert 'href="/pull"' not in body
        assert team.get("/pull").status_code == 404
        _push(team, CAR)
        assert (
            '<a class="pull" href="/pull" download>Pull</a>' in team.get("/push").text
        )
        response = team.get("/pull")
        assert response.status_code == 200
        assert response.text == CAR
        assert response.headers["content-disposition"] == (
            'attachment; filename="my_car.py"'
        )

    def test_a_refused_push_pulls_the_previous_controller(
        self, team: TestClient
    ) -> None:
        _push(team, CAR)
        _push(team, BROKEN)
        assert team.get("/pull").text == CAR

    def test_a_stranger_cannot_pull(self, client: TestClient) -> None:
        response = client.get("/pull", follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/"

    def test_a_file_that_is_not_text_is_refused(
        self, team: TestClient, store: Store
    ) -> None:
        _push(team, b"\xff\xfe\x00binary")
        assert store.teams() == []
        assert "pas du texte UTF-8" in team.get("/push").text

    def test_an_oversized_file_is_refused(self, team: TestClient, store: Store) -> None:
        _push(team, CAR.encode() + b"#" * MAX_SOURCE_BYTES)
        assert store.teams() == []
        assert "dépasse 256 Ko" in team.get("/push").text

    def test_an_empty_push_is_refused(self, team: TestClient, store: Store) -> None:
        _push(team, "   ")
        assert store.teams() == []
        assert "Le contrôleur est vide." in team.get("/push").text

    def test_no_file_at_all_is_refused(self, team: TestClient, store: Store) -> None:
        _push(team, None)
        assert store.teams() == []
        assert "Le contrôleur est vide." in team.get("/push").text

    def test_a_signed_out_push_is_refused(
        self, client: TestClient, store: Store
    ) -> None:
        response = _push(client, CAR)
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        assert store.accounts() == []

    def test_the_admin_cannot_push(self, admin: TestClient, store: Store) -> None:
        assert _push(admin, CAR).headers["location"] == "/"
        assert store.teams() == []


def _describe(client: TestClient, number: int | str, description: str) -> Response:
    """Save a version's description through its row's form on the push tab."""
    response: Response = client.post(
        f"/versions/{number}/description",
        data={"description": description},
        follow_redirects=False,
    )
    return response


def _versions(body: str) -> str:
    """The push tab's versions table, from its heading to its end."""
    start = body.index("<h2>Versions</h2>")
    return body[start : body.index("</section>", start)]


class TestVersions:
    def test_any_version_is_pulled_back_as_my_car(self, team: TestClient) -> None:
        sources = [CAR + f"# take {n}\n" for n in (1, 2, 3)]
        for source in sources:
            _push(team, source)
        assert team.get("/pull?version=1").text == sources[0]
        assert team.get("/pull?version=2").text == sources[1]
        assert team.get("/pull").text == sources[2]
        response = team.get("/pull?version=1")
        assert response.headers["content-disposition"] == (
            'attachment; filename="my_car.py"'
        )

    def test_a_version_the_team_does_not_have_is_a_404(
        self, team: TestClient, store: Store
    ) -> None:
        _push(team, CAR)
        enter(store, "bravo", CAR + "# bravo's secret\n")
        store.push(key(store, "bravo"), CAR + "# bravo's second secret\n")
        for number in ("2", "0", "-1"):
            response = team.get(f"/pull?version={number}")
            assert response.status_code == 404
            assert "secret" not in response.text
        assert team.get("/pull?version=two").status_code == 422

    def test_a_stranger_cannot_pull_a_version(self, client: TestClient) -> None:
        response = client.get("/pull?version=1", follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/"

    def test_the_table_is_hidden_before_the_first_push(self, team: TestClient) -> None:
        assert "<h2>Versions</h2>" not in team.get("/push").text
        _push(team, CAR)
        assert "<h2>Versions</h2>" in team.get("/push").text

    def test_each_row_pulls_its_version(self, team: TestClient) -> None:
        _push(team, CAR)
        _push(team, WARNED)
        table = _versions(team.get("/push").text)
        assert table.index('href="/pull?version=2"') < table.index(
            'href="/pull?version=1"'
        )

    def test_a_description_is_edited_everywhere_it_shows(
        self, team: TestClient, store: Store, scheduler: Scheduler
    ) -> None:
        _push(team, CAR, "first")
        _push(team, WARNED, "second")
        scheduler.run_qualification()  # races v2
        _push(team, CAR + "# v3\n")
        response = _describe(team, 2, "freinage <tardif>")
        assert response.status_code == 303
        assert response.headers["location"] == "/push"
        (alpha,) = store.teams()
        assert [(v.number, v.description) for v in alpha.versions] == [
            (1, "first"),
            (2, "freinage <tardif>"),
            (3, ""),
        ]
        assert store.version_source(key(store, "alpha"), 2) == WARNED
        assert 'value="freinage &lt;tardif&gt;"' in _versions(team.get("/push").text)
        race = team.get("/replays/1").text
        assert "— freinage &lt;tardif&gt;" in race

    def test_an_empty_description_clears_it(
        self, team: TestClient, store: Store
    ) -> None:
        _push(team, CAR, "first")
        _describe(team, 1, "")
        assert store.teams()[0].versions[0].description == ""

    def test_an_overlong_description_is_refused_on_the_push_tab(
        self, team: TestClient, store: Store
    ) -> None:
        _push(team, CAR, "first")
        assert _describe(team, 1, "x" * (MAX_DESCRIPTION_CHARS + 1)).status_code == 303
        assert store.teams()[0].versions[0].description == "first"
        body = team.get("/push").text
        assert "La description dépasse 200 caractères." in _versions(body)

    def test_a_refusal_is_not_handed_to_a_team_that_takes_the_name(
        self, team: TestClient, store: Store, app: FastAPI
    ) -> None:
        _push(team, CAR, "first")
        _describe(team, 1, "x" * (MAX_DESCRIPTION_CHARS + 1))
        store.delete("alpha")
        with TestClient(app) as new:
            _signup(new, "alpha", "other")
            _push(new, CAR)
            assert "dépasse" not in new.get("/push").text

    def test_only_the_team_s_own_versions_are_described(
        self, team: TestClient, store: Store
    ) -> None:
        _push(team, CAR)
        enter(store, "bravo")
        store.push(key(store, "bravo"), CAR + "# v2\n", "bravo's own")
        _describe(team, 2, "hijacked")
        bravo = store.account("bravo")
        assert bravo is not None
        assert bravo.versions[1].description == "bravo's own"
        assert "Version inconnue." in team.get("/push").text

    def test_a_stranger_or_the_admin_cannot_describe(
        self, client: TestClient, store: Store
    ) -> None:
        enter(store, "bravo")
        response = _describe(client, 1, "stranger")
        assert response.status_code == 303 and response.headers["location"] == "/"
        assert _admin_login(client, PASSWORD).status_code == 303
        assert _describe(client, 1, "admin").headers["location"] == "/"
        assert store.teams()[0].versions[0].description == ""

    def test_the_race_page_links_the_version_to_pull_it(
        self, team: TestClient, scheduler: Scheduler
    ) -> None:
        _push(team, CAR)
        scheduler.run_qualification()
        race = team.get("/replays/1").text
        assert "Ta version : <a href='/pull?version=1' download>v1</a>" in race


class TestSignedOut:
    """Nothing is public: every tab sends a stranger to the sign-in page,
    and every API, static file and action answers 401."""

    TABS = ("/leaderboard", "/replays", "/replays/1", "/push", "/live", "/admin")

    def test_the_root_is_the_sign_in_page(self, client: TestClient) -> None:
        body = client.get("/").text
        assert 'action="/login"' in body and 'href="/signup"' in body
        assert "Classement Qualif" not in body

    def test_every_tab_redirects_to_the_sign_in_page(self, client: TestClient) -> None:
        for path in self.TABS:
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 303, path
            assert response.headers["location"] == "/", path

    def test_every_api_answers_401(self, client: TestClient) -> None:
        for path in (
            "/api/standings",
            "/api/races/1/replay",
            "/static/replay.js",
            "/static/replay.css",
        ):
            assert client.get(path).status_code == 401, path

    def test_the_sign_up_page_is_open(self, client: TestClient) -> None:
        body = client.get("/signup").text
        assert 'action="/signup"' in body and "Créer l'équipe" in body


class TestStartKit:
    """`/start-kit.zip` is open to everyone, and every page links it."""

    LINK = 'href="/start-kit.zip" download'

    def test_the_sign_in_page_offers_it(self, client: TestClient) -> None:
        body = client.get("/").text
        assert self.LINK in body and "Kit de départ" in body

    def test_a_stranger_downloads_the_shipped_kit(self, client: TestClient) -> None:
        response = client.get("/start-kit.zip")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert 'filename="start_kit.zip"' in response.headers["content-disposition"]
        names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
        assert "start_kit/my_car.py" in names and "start_kit/cocoracer.py" in names

    def test_caches_and_dotfiles_are_left_out(
        self, store: Store, scheduler: Scheduler, tmp_path: Path
    ) -> None:
        (tmp_path / "my_car.py").write_text("x = 1\n")
        (tmp_path / "img").mkdir()
        (tmp_path / "img" / "pose.png").write_bytes(b"png")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "my_car.cpython-312.pyc").write_bytes(b"")
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "pyvenv.cfg").write_text("")
        (tmp_path / ".DS_Store").write_bytes(b"")
        app = create_app(store, scheduler, PASSWORD, start_kit=tmp_path)
        with TestClient(app) as client:
            archive = zipfile.ZipFile(io.BytesIO(client.get("/start-kit.zip").content))
        assert archive.namelist() == ["start_kit/img/pose.png", "start_kit/my_car.py"]
        assert archive.read("start_kit/my_car.py") == b"x = 1\n"

    def test_the_markdown_names_the_server_it_was_downloaded_from(
        self, store: Store, scheduler: Scheduler, tmp_path: Path
    ) -> None:
        (tmp_path / "wtf.md").write_text("Va sur `{{SERVER_ADDRESS}}`.\n")
        (tmp_path / "my_car.py").write_text("s = '{{SERVER_ADDRESS}}'\n")
        app = create_app(store, scheduler, PASSWORD, start_kit=tmp_path)
        with TestClient(app, base_url="http://192.168.1.20:8080") as client:
            archive = zipfile.ZipFile(io.BytesIO(client.get("/start-kit.zip").content))
        assert archive.read("start_kit/wtf.md") == b"Va sur `192.168.1.20:8080`.\n"
        assert archive.read("start_kit/my_car.py") == b"s = '{{SERVER_ADDRESS}}'\n"

    def test_through_the_proxy_the_markdown_names_its_https_origin(
        self, store: Store, scheduler: Scheduler, tmp_path: Path
    ) -> None:
        (tmp_path / "wtf.md").write_text("Va sur `{{SERVER_ADDRESS}}`.\n")
        app = create_app(store, scheduler, PASSWORD, start_kit=tmp_path)
        with TestClient(app, base_url="https://cocoracer.example.org") as client:
            response = client.get(
                "/start-kit.zip", headers={"X-Forwarded-Proto": "https"}
            )
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        assert archive.read("start_kit/wtf.md") == (
            b"Va sur `https://cocoracer.example.org`.\n"
        )

    def test_the_shipped_tutorial_names_the_server_by_its_placeholder(self) -> None:
        assert SERVER_PLACEHOLDER in (REPO_START_KIT / "wtf.md").read_text()

    def test_a_server_without_a_kit_answers_404(
        self, store: Store, scheduler: Scheduler, tmp_path: Path
    ) -> None:
        app = create_app(store, scheduler, PASSWORD, start_kit=tmp_path / "missing")
        with TestClient(app) as client:
            assert client.get("/start-kit.zip").status_code == 404

    def test_a_team_has_it_in_the_header(self, team: TestClient) -> None:
        header = _header(team.get("/leaderboard").text)
        assert self.LINK in header and 'aria-label="Kit de départ"' in header

    def test_the_admin_has_it_in_the_header(self, admin: TestClient) -> None:
        assert self.LINK in _header(admin.get("/leaderboard").text)
        assert 'aria-label="Start kit"' in _header(admin.get("/admin").text)


def _header(body: str) -> str:
    return body[body.index('<header class="top">') : body.index("</header>")]


class TestTabs:
    def test_a_team_lands_on_the_leaderboard(self, team: TestClient) -> None:
        response = team.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/leaderboard"

    def test_the_admin_lands_on_the_leaderboard(self, admin: TestClient) -> None:
        assert admin.get("/", follow_redirects=False).headers["location"] == (
            "/leaderboard"
        )

    def test_the_team_and_the_admin_have_different_tabs(self, app: FastAPI) -> None:
        with TestClient(app) as laptop:
            _signup(laptop, "bravo", "pw")
            mine = _tabs(laptop.get("/leaderboard").text)
        with TestClient(app) as projector:
            _admin_login(projector, PASSWORD)
            theirs = _tabs(projector.get("/leaderboard").text)
        assert mine == ["/leaderboard", "/live", "/replays", "/push"]
        assert theirs == ["/leaderboard", "/live", "/replays", "/admin"]

    def test_the_tab_labels(self, app: FastAPI) -> None:
        with TestClient(app) as laptop:
            _signup(laptop, "bravo", "pw")
            mine = _labels(laptop.get("/leaderboard").text)
        with TestClient(app) as projector:
            _admin_login(projector, PASSWORD)
            theirs = _labels(projector.get("/leaderboard").text)
        assert mine == ["Classement", "Live", "Replay", "Push"]
        assert theirs == ["Classement", "Live", "Replay", "Admin"]

    def test_every_tab_of_a_team_answers(self, team: TestClient) -> None:
        for path in ("/leaderboard", "/live", "/replays", "/push"):
            assert team.get(path).status_code == 200, path

    def test_a_team_is_turned_away_from_the_admin_tab(self, team: TestClient) -> None:
        response = team.get("/admin", follow_redirects=False)
        assert response.headers["location"] == "/"

    def test_the_admin_has_no_push_tab(self, admin: TestClient) -> None:
        assert admin.get("/push", follow_redirects=False).headers["location"] == "/"

    def test_the_active_tab_is_marked(self, team: TestClient) -> None:
        body = team.get("/replays").text
        assert "<a href='/replays' aria-current='page'>Replay</a>" in body
        assert "<a href='/push'>Push</a>" in body

    def test_the_header_says_who_is_signed_in(self, team: TestClient) -> None:
        body = team.get("/leaderboard").text
        assert "Équipe <strong>alpha</strong>" in body
        assert 'action="/logout"' in body and "id='status'" in body
        assert '<a class="brand" href="/">Défi vroom vroom</a>' in body

    def test_no_tab_reloads_itself(self, team: TestClient) -> None:
        body = team.get("/leaderboard").text
        assert 'http-equiv="refresh"' not in body
        assert "data-live" in body

    def test_a_signed_in_team_skips_the_sign_up_page(self, team: TestClient) -> None:
        assert team.get("/signup", follow_redirects=False).headers["location"] == "/"

    def test_signing_in_as_one_signs_out_of_the_other(self, admin: TestClient) -> None:
        _signup(admin, "alpha", "pw")
        assert admin.get("/admin", follow_redirects=False).status_code == 303
        _admin_login(admin, PASSWORD)
        assert admin.get("/push", follow_redirects=False).status_code == 303
        assert admin.get("/admin").status_code == 200


def _tabs(body: str) -> list[str]:
    """The paths of a page's tabs, in header order."""
    nav = body[body.index('<nav class="tabs"') : body.index("</nav>")]
    return [part.split("'", 1)[0] for part in nav.split("<a href='")[1:]]


def _labels(body: str) -> list[str]:
    """The labels of a page's tabs, in header order."""
    nav = body[body.index('<nav class="tabs"') : body.index("</nav>")]
    return [part.split(">", 1)[1].split("<", 1)[0] for part in nav.split("<a ")[1:]]


class TestTeamTabs:
    def test_a_race_shows_up_in_the_replays_tab(
        self, team: TestClient, store: Store, scheduler: Scheduler
    ) -> None:
        _push(team, CAR)
        enter(store, "bravo")
        # Grid alpha, bravo (no shuffle); the field finishes reversed.
        scheduler.run_qualification()
        body = team.get("/replays").text
        history = body[body.index('<section id="history"') :]
        assert "href='/replays/1'" in history
        row = history[history.index("<tr><td><a href='/replays/1'>") :]
        row = row[: row.index("</tr>")]
        cells = [c.split(">", 1)[1].split("<", 1)[0] for c in row.split("<td")[1:]]
        assert cells[1:8] == [
            "Qualification",
            cells[2],
            "v1",
            "1",
            "2",
            "-1",
            "terminé",
        ]

    def test_the_team_line_is_picked_out_on_the_board(self, team: TestClient) -> None:
        _push(team, CAR)
        body = team.get("/leaderboard").text
        assert "<tr class='me'><td>1</td><td>alpha</td>" in body

    def test_both_boards_appear_before_any_official_race(
        self, team: TestClient
    ) -> None:
        body = team.get("/leaderboard").text
        assert "Classement Qualif" in body
        assert "Classement courses officielles" in body
        assert "Aucune course officielle pour l'instant." in body


class TestReplay:
    @pytest.fixture(params=["in memory", "on disk"])
    def store(
        self, request: pytest.FixtureRequest, tmp_path: Path, teams: tuple[str, ...]
    ) -> Store:
        """Every replay test runs against both ways the store keeps one."""
        store = Store(state_dir=tmp_path if request.param == "on disk" else None)
        for name in teams:
            enter(store, name)
        return store

    @pytest.fixture
    def raced(
        self,
        team: TestClient,
        store: Store,
        runner: RecordingRunner,
        scheduler: Scheduler,
    ) -> TestClient:
        """alpha, signed in, after one recorded race and one void one."""
        _push(team, CAR)
        runner.outcome = RaceOutcome(
            classified=("alpha",),
            statuses={"alpha": "finished"},
            replay=REPLAY_BYTES,
        )
        scheduler.run_qualification()
        runner.outcome = RaceOutcome.voided("stuck")
        scheduler.run_qualification()
        return team

    def test_the_recording_is_served_as_json(self, raced: TestClient) -> None:
        response = raced.get("/api/races/1/replay")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        assert response.headers["cache-control"] == "private, max-age=86400"
        assert response.content == REPLAY_BYTES
        assert response.json() == REPLAY

    def test_a_void_race_has_no_recording(self, raced: TestClient) -> None:
        assert raced.get("/api/races/2/replay").status_code == 404

    def test_an_unknown_race_has_no_recording(self, raced: TestClient) -> None:
        assert raced.get("/api/races/3/replay").status_code == 404
        assert raced.get("/api/races/0/replay").status_code == 404

    def test_replays_need_a_signed_in_team(
        self, raced: TestClient, app: FastAPI
    ) -> None:
        with TestClient(app) as stranger:
            assert stranger.get("/api/races/1/replay").status_code == 401
            page = stranger.get("/replays/1", follow_redirects=False)
            assert page.status_code == 303

    def test_the_admin_can_watch_replays(self, raced: TestClient, app: FastAPI) -> None:
        with TestClient(app) as admin:
            _admin_login(admin, PASSWORD)
            assert admin.get("/api/races/1/replay").status_code == 200
            assert "ReplayPlayer.mount(" in admin.get("/replays/1").text

    def test_the_admin_lists_every_race_with_its_field(
        self, raced: TestClient, app: FastAPI
    ) -> None:
        with TestClient(app) as admin:
            _admin_login(admin, PASSWORD)
            body = admin.get("/replays").text
        races = body[body.index('<section id="races"') :]
        assert races.index("/replays/2") < races.index("/replays/1")
        assert "<td>alpha</td>" in races and "annulée" in races

    def test_the_race_page_is_a_wide_replays_tab(self, raced: TestClient) -> None:
        body = raced.get("/replays/1").text
        assert "<main class='wide'>" in body
        assert "aria-current='page'>Replay</a>" in body

    def test_the_race_page_mounts_the_player(self, raced: TestClient) -> None:
        body = raced.get("/replays/1").text
        assert "/static/replay.js" in body and "/static/replay.css" in body
        assert "ReplayPlayer.mount(" in body and "/api/races/1/replay" in body

    def test_the_void_race_page_says_so(self, raced: TestClient) -> None:
        body = raced.get("/replays/2").text
        assert "Course annulée" in body and "ReplayPlayer.mount(" not in body

    def test_an_unknown_race_page_is_a_404(self, raced: TestClient) -> None:
        response = raced.get("/replays/9")
        assert response.status_code == 404
        assert "Course introuvable" in response.text

    def test_the_player_is_served(self, team: TestClient) -> None:
        assert team.get("/static/replay.js").status_code == 200
        assert team.get("/static/replay.css").status_code == 200

    @pytest.fixture
    def logged(
        self,
        team: TestClient,
        store: Store,
        runner: RecordingRunner,
        scheduler: Scheduler,
    ) -> TestClient:
        """alpha, signed in, after a race with beta in which both printed,
        and in whose logs a gamma that signs up later is named."""
        enter(store, "beta")
        _push(team, CAR)
        runner.outcome = RaceOutcome(
            classified=("alpha", "beta"),
            statuses={"alpha": "finished", "beta": "finished"},
            replay=REPLAY_BYTES,
            logs={"alpha": ALPHA_LOG, "beta": BETA_LOG, "gamma": BETA_LOG},
        )
        scheduler.run_qualification()
        return team

    def test_a_team_reads_its_own_cars_log(self, logged: TestClient) -> None:
        response = logged.get("/api/races/1/logs")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == ALPHA_LOG

    def test_no_team_reads_another_teams_log(
        self, logged: TestClient, app: FastAPI
    ) -> None:
        assert "secret" not in logged.get("/api/races/1/logs").text
        with TestClient(app) as beta:
            _login(beta, "beta", "pw")
            assert beta.get("/api/races/1/logs").json() == BETA_LOG

    def test_a_team_that_signed_up_after_the_race_reads_no_log(
        self, logged: TestClient, app: FastAPI
    ) -> None:
        """A name taken again after a deletion is not the old team."""
        with TestClient(app) as gamma:
            _signup(gamma, "gamma", "pw")
            assert gamma.get("/api/races/1/logs").json() == NO_LOG

    def test_a_car_that_printed_nothing_has_an_empty_log(
        self, raced: TestClient
    ) -> None:
        assert raced.get("/api/races/1/logs").json() == NO_LOG
        assert raced.get("/api/races/2/logs").json() == NO_LOG
        assert raced.get("/api/races/9/logs").status_code == 404

    def test_logs_are_for_teams_only(self, logged: TestClient, app: FastAPI) -> None:
        """The admin's screen is the projector."""
        with TestClient(app) as admin:
            _admin_login(admin, PASSWORD)
            assert admin.get("/api/races/1/logs").status_code == 401
            assert "/api/races/1/logs" not in admin.get("/replays/1").text
        with TestClient(app) as stranger:
            assert stranger.get("/api/races/1/logs").status_code == 401

    def test_the_race_page_shows_the_log_to_a_team_that_raced(
        self, logged: TestClient, app: FastAPI
    ) -> None:
        body = logged.get("/replays/1").text
        assert '{"log": "/api/races/1/logs", "me": "alpha"}' in body
        with TestClient(app) as gamma:
            _signup(gamma, "gamma", "pw")
            assert "/api/races/1/logs" not in gamma.get("/replays/1").text

    def test_a_team_that_takes_a_deleted_name_has_none_of_its_races(
        self, logged: TestClient, store: Store, app: FastAPI
    ) -> None:
        store.delete("alpha")
        with TestClient(app) as new:
            _signup(new, "alpha", "other")
            assert new.get("/api/races/1/logs").json() == NO_LOG
            page = new.get("/replays/1").text
            assert "/api/races/1/logs" not in page and '"me"' not in page
            assert "Rien pour l'instant." in new.get("/replays").text

    def test_the_standings_carry_no_replay(self, raced: TestClient) -> None:
        assert '"frames"' not in raced.get("/api/standings").text
        assert "centerline" not in raced.get("/leaderboard").text


class TestLeaderboards:
    def test_the_leaderboard_tab_renders_both_boards(
        self, admin: TestClient, store: Store
    ) -> None:
        enter(store, "alpha")
        body = admin.get("/leaderboard").text
        assert "alpha" in body and "Classement Qualif" in body
        assert "Classement courses officielles" in body

    def test_the_api_reports_both_scores(
        self, admin: TestClient, scheduler: Scheduler, store: Store
    ) -> None:
        enter(store, "alpha")
        enter(store, "bravo")
        scheduler.run_qualification()
        payload = admin.get("/api/standings").json()
        assert payload["races"] == 1
        assert payload["running"] is None
        assert payload["paused"] is False and payload["next_race_in"] is None
        scores = {row["team"]: row for row in payload["standings"]}
        # Grid is registration order (shuffle is a no-op here) and the field
        # finishes reversed, so bravo gains one place.
        assert scores["bravo"]["qualification_score"] == 1
        assert scores["alpha"]["qualification_score"] == 0

    def test_the_api_says_when_a_race_is_running(
        self, team: TestClient, runner: RecordingRunner, scheduler: Scheduler
    ) -> None:
        """The live tab polls this to know when to load the race view."""
        _push(team, CAR)
        runner.gate = threading.Event()
        race = threading.Thread(target=scheduler.run_qualification)
        race.start()
        try:
            assert runner.started.wait(5.0)
            assert team.get("/api/standings").json()["running"] == "qualification"
            assert "course de qualification en cours" in team.get("/live").text
        finally:
            runner.gate.set()
            race.join(5.0)

    def test_the_api_gives_the_countdown(
        self, team: TestClient, scheduler: Scheduler
    ) -> None:
        scheduler.resume()
        remaining = team.get("/api/standings").json()["next_race_in"]
        assert 0 < remaining <= scheduler.interval
        scheduler.pause()
        payload = team.get("/api/standings").json()
        assert payload["paused"] is True and payload["next_race_in"] is None

    def test_the_api_gives_a_team_that_takes_a_deleted_name_nothing(
        self, admin: TestClient, scheduler: Scheduler, store: Store
    ) -> None:
        enter(store, "alpha")
        enter(store, "bravo")
        scheduler.run_qualification()  # bravo +1
        scheduler.run_official()

        def rows() -> dict[str, dict[str, object]]:
            payload = admin.get("/api/standings").json()
            return {row["team"]: row for row in payload["standings"]}

        before = rows()
        assert before["bravo"]["qualification_score"] == 1
        admin.post("/admin/delete-team", data={"team": "bravo"}, follow_redirects=False)
        enter(store, "bravo", password="other")
        after = rows()
        assert after["bravo"] == {
            "team": "bravo",
            "qualification_score": 0,
            "official_score": None,
            "best_official": None,
        }
        assert after["alpha"] == before["alpha"]


class TestLiveTab:
    def test_headless_there_is_no_live_view(self, team: TestClient) -> None:
        body = team.get("/live").text
        assert "Pas de course en direct" in body
        assert "ReplayPlayer.live(" not in body

    def test_live_the_player_connects_on_the_page_host(
        self, store: Store, runner: RecordingRunner
    ) -> None:
        scheduler = Scheduler(store, runner=runner, live=True, port=8123)
        app = create_app(store=store, scheduler=scheduler, admin_password=PASSWORD)
        with TestClient(app, base_url="http://10.0.0.7:8000") as laptop:
            _signup(laptop, "alpha", "pw")
            body = laptop.get("/live").text
        assert "ReplayPlayer.live(" in body
        assert "data-ws='ws://10.0.0.7:8123/ws'" in body
        assert "/static/replay.js" in body and "<iframe" not in body

    def test_through_the_proxy_the_player_connects_on_the_page_origin(
        self, store: Store, runner: RecordingRunner
    ) -> None:
        scheduler = Scheduler(store, runner=runner, live=True, port=8123)
        app = create_app(store=store, scheduler=scheduler, admin_password=PASSWORD)
        proxied = {"X-Forwarded-Proto": "https"}
        with TestClient(
            app, base_url="https://cocoracer.example.org", headers=proxied
        ) as browser:
            _signup(browser, "alpha", "pw")
            body = browser.get("/live").text
        assert "data-ws='wss://cocoracer.example.org/live/ws'" in body

    def test_the_admin_has_it_too(self, admin: TestClient) -> None:
        assert admin.get("/live").status_code == 200


class TestPlay:
    """A launched official race waits on the grid for the admin's Play,
    which only the admin is offered, on Live."""

    @pytest.fixture
    def live_app(self, store: Store) -> FastAPI:
        def waits_for_play(*args: object, **kwargs: object) -> RaceOutcome:
            play = kwargs.get("play")
            assert isinstance(play, threading.Event)
            assert play.wait(5.0), "the test never pressed Play"
            return RaceOutcome(classified=("alpha",), statuses={"alpha": "finished"})

        scheduler = Scheduler(store, runner=waits_for_play, live=True, port=8123)
        return create_app(store=store, scheduler=scheduler, admin_password=PASSWORD)

    def test_the_admin_launches_presses_play_and_the_race_runs(
        self, live_app: FastAPI, store: Store
    ) -> None:
        enter(store, "alpha")
        with TestClient(live_app) as admin:
            _admin_login(admin, PASSWORD)
            launched = admin.post("/admin/official-race")
            assert launched.url.path == "/live"
            assert _eventually(
                lambda: "action='/admin/play'" in admin.get("/live").text
            )
            assert store.races() == []
            pressed = admin.post("/admin/play", follow_redirects=False)
            assert pressed.status_code == 303
            assert pressed.headers["location"] == "/live"
            assert _eventually(lambda: len(store.races()) == 1)
            assert "action='/admin/play'" not in admin.get("/live").text
            again = admin.post("/admin/play", follow_redirects=False)
            assert again.status_code == 303
            assert again.headers["location"] == "/live"

    def test_a_team_sees_the_grid_but_no_play(
        self, live_app: FastAPI, store: Store
    ) -> None:
        enter(store, "bravo")
        with TestClient(live_app) as admin, TestClient(live_app) as team:
            _admin_login(admin, PASSWORD)
            _signup(team, "alpha", "pw")
            admin.post("/admin/official-race", follow_redirects=False)
            assert _eventually(
                lambda: "action='/admin/play'" in admin.get("/live").text
            )
            body = team.get("/live").text
            assert "ReplayPlayer.live(" in body
            assert "/admin/play" not in body
            assert team.post("/admin/play", follow_redirects=False).status_code == 401
            assert store.races() == []
            admin.post("/admin/play", follow_redirects=False)
            assert _eventually(lambda: len(store.races()) == 1)

    def test_play_with_nothing_waiting_just_lands_on_live(
        self, admin: TestClient, scheduler: Scheduler
    ) -> None:
        response = admin.post("/admin/play", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/live"
        assert not scheduler.awaiting_play


class TestAdmin:
    def test_a_wrong_password_does_not_sign_in(self, client: TestClient) -> None:
        response = _admin_login(client, "guess")
        assert response.status_code == 401
        assert "Wrong admin password." in response.text
        # The form comes back on the admin side of the switch.
        assert re.search(r'value="admin"\s+checked', response.text)
        assert _signed_out(client)

    def test_the_admin_ignores_the_team_name(self, client: TestClient) -> None:
        response = client.post(
            "/login",
            data={"role": "admin", "team": "whoever", "password": PASSWORD},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert client.get("/admin").status_code == 200

    def test_the_cookie_does_not_carry_the_admin_password(
        self, client: TestClient
    ) -> None:
        """A browser left open on the projector must not hold the password."""
        response = _admin_login(client, PASSWORD)
        assert PASSWORD not in response.cookies[ADMIN_COOKIE]
        assert client.get("/admin").status_code == 200

    def test_the_password_itself_is_not_a_session_cookie(
        self, client: TestClient
    ) -> None:
        client.cookies.set(ADMIN_COOKIE, PASSWORD)
        assert client.get("/admin", follow_redirects=False).status_code == 303

    def test_the_admin_tab_gives_the_controls(self, admin: TestClient) -> None:
        body = admin.get("/admin").text
        assert "Start an official race" in body
        assert "<strong>Admin</strong>" in body

    def test_signing_out(self, admin: TestClient) -> None:
        admin.post("/logout")
        assert _signed_out(admin)

    def test_admin_actions_are_refused_without_the_password(
        self, client: TestClient
    ) -> None:
        for path in (
            "/admin/pause",
            "/admin/resume",
            "/admin/official-race",
            "/admin/lock-signups",
            "/admin/unlock-signups",
        ):
            assert client.post(path, follow_redirects=False).status_code == 401

    def test_pausing_and_resuming_the_cadence(
        self, admin: TestClient, scheduler: Scheduler
    ) -> None:
        admin.post("/admin/pause", follow_redirects=False)
        assert scheduler.paused
        admin.post("/admin/resume", follow_redirects=False)
        assert not scheduler.paused

    def test_an_official_race_leads_to_live(
        self, admin: TestClient, store: Store
    ) -> None:
        enter(store, "alpha")
        enter(store, "bravo")
        response = admin.post("/admin/official-race", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/live"
        assert _eventually(lambda: len(store.races()) == 1)

    def test_the_request_does_not_wait_for_the_race(
        self, admin: TestClient, runner: RecordingRunner, store: Store
    ) -> None:
        """An official race takes minutes; the admin page comes back now."""
        enter(store, "alpha")
        runner.gate = threading.Event()
        try:
            assert (
                admin.post("/admin/official-race", follow_redirects=False).status_code
                == 303
            )
            assert runner.started.wait(5.0)
            assert store.races() == []
        finally:
            runner.gate.set()
        assert _eventually(lambda: len(store.races()) == 1)

    def test_a_second_official_race_is_refused_while_one_is_running(
        self, admin: TestClient, runner: RecordingRunner, store: Store
    ) -> None:
        enter(store, "alpha")
        runner.gate = threading.Event()
        try:
            admin.post("/admin/official-race", follow_redirects=False)
            assert runner.started.wait(5.0)
            second = admin.post("/admin/official-race", follow_redirects=False)
            assert second.status_code == 409
            assert "location" not in second.headers
            assert "already running" in second.json()["detail"]
        finally:
            runner.gate.set()
        assert _eventually(lambda: len(store.races()) == 1)

    def test_locking_and_unlocking_team_creation(
        self, admin: TestClient, store: Store
    ) -> None:
        enter(store, "alpha")
        admin.post("/admin/lock-signups", follow_redirects=False)
        assert store.signups_locked
        refused = TestClient(admin.app)
        response = _signup(refused, "bravo", "pw")
        assert response.status_code == 409
        assert "inscriptions sont fermées" in response.text
        assert [t.name for t in store.accounts()] == ["alpha"]
        admin.post("/admin/unlock-signups", follow_redirects=False)
        assert not store.signups_locked
        assert _signup(refused, "bravo", "pw").status_code == 303
        assert [t.name for t in store.accounts()] == ["alpha", "bravo"]

    def test_deleting_a_team_removes_it_from_the_field(
        self, admin: TestClient, store: Store
    ) -> None:
        enter(store, "alpha")
        admin.post("/admin/delete-team", data={"team": "alpha"}, follow_redirects=False)
        assert store.teams() == []

    def test_a_team_with_no_submission_can_be_deleted(
        self, admin: TestClient, store: Store
    ) -> None:
        store.register("alpha", "lost it")
        assert "Delete alpha" in admin.get("/admin").text
        admin.post("/admin/delete-team", data={"team": "alpha"}, follow_redirects=False)
        assert store.accounts() == []

    def test_a_deleted_name_can_register_again_with_a_new_password(
        self, admin: TestClient, store: Store
    ) -> None:
        enter(store, "alpha")
        admin.post("/admin/delete-team", data={"team": "alpha"}, follow_redirects=False)
        assert _signup(admin, "alpha", "fresh").status_code == 303
