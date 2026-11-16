"""The event's whole state.

Teams, every version they pushed and their last push's feedback, and every
finished race. Given a state directory, the store rewrites `state.json` in it after
every mutation that changes what is kept, and loads it back at startup, so a
restart, a redeploy or a reboot does not erase the event
(`docs/adr/0003-event-state-persists-on-disk.md`). What it keeps: the teams
(password hash, and each version's number, description and time), the race
reports, and whether sign-ups are locked. Each version's source is its own
file, `controllers/<team key>/<number>.py`, written before the state file
names the version, so the state file stays small however often teams push;
memory holds only each team's latest source, which every race needs. The
team key (`TeamKey`) is when the team signed up, not its name, which a
deleted team leaves free for another; memory keys teams by it too.
Each race's replay, the bulk of the event's data, is written once to its own
file, `replays/<index>.json`, and served from there; memory holds only which
races have one. What the cars printed in it, their logs, is written the same
way to `logs/<index>.json`, apart from the replay because a team reads only
its own car's log. What a restart drops: push feedback and anything outside the
store (sessions, the cadence timer, a race in flight). Without a state
directory nothing is written and a restart loses everything, which is what
the tests and a dev run want; the replays are then kept in memory, as the
bytes the runner serialised, so no request ever serialises one again, and so
is every version's source. A team's password is never kept: only a
salted scrypt hash of it, which is all sign-in needs. The store is the only
mutable state in the server, and every mutation goes through a method here
so the scheduler and the HTTP layer never race each other over a dictionary.
"""

import hashlib
import json
import math
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from cocoracer_server.analysis import Analysis, check_source
from cocoracer_server.scoring import (
    RaceKind,
    RaceRecord,
    Standing,
    final_standings,
    race_gain,
)

MAX_TEAMS = 16
MAX_NAME_CHARS = 32
MAX_SOURCE_BYTES = 256 * 1024
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]*$")
TOO_LARGE = f"Le contrôleur dépasse {MAX_SOURCE_BYTES // 1024} Ko."
MAX_DESCRIPTION_CHARS = 200

# scrypt's cost for team passwords. Sign-in hashes on the request thread, so
# the cost is set to verify in about 50 ms on the event laptop (16 MB of
# memory each). Each hash records the parameters it was made with, so
# raising these later leaves existing accounts able to sign in.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
_SALT_BYTES = 16

STATE_FILE = "state.json"
REPLAY_DIR = "replays"
LOG_DIR = "logs"
CONTROLLER_DIR = "controllers"
# Bumped when the file's shape changes; a file with any other version is
# refused rather than guessed at. Version 1 kept one source per team, in the
# file itself; it still loads, as that source's single version.
STATE_VERSION = 2


class StateError(Exception):
    """The state file exists but cannot be loaded. The server must not
    start empty over it, so this stops it, naming the file."""


class Rejected(Exception):
    """A request the store refuses, with the reason the team should see.

    `detail` is in French: it goes straight onto the team's page.
    """

    def __init__(self, detail: str, status_code: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class SignedOut(Rejected):
    """A per-team call for a team that is no longer signed up: the admin
    deleted it, perhaps after the caller checked its session, and its name
    may already be another team's. The caller treats it as a session that
    has ended."""

    def __init__(self) -> None:
        super().__init__("Ton équipe n'existe plus : reconnecte-toi.", 401)


class PushRejected(Rejected):
    """A push the store refuses. `analysis` is set when static analysis
    found errors, so the page can list them."""

    def __init__(
        self, detail: str, status_code: int, analysis: Analysis | None = None
    ) -> None:
        super().__init__(detail, status_code)
        self.analysis = analysis


@dataclass(frozen=True)
class Version:
    """One accepted push of a team, numbered from 1 for that team.

    The description is the team's own note, optional and changeable later.
    The source is kept apart (`Store.version_source`): a team's versions are
    read far more often than any but the latest one's code.
    """

    number: int
    description: str
    pushed_at: float

    @property
    def label(self) -> str:
        return f"v{self.number}"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Version":
        return cls(
            number=int(data["number"]),
            description=str(data["description"]),
            pushed_at=float(data["pushed_at"]),
        )


@dataclass(frozen=True)
class TeamKey:
    """Which team a per-team call is about: its sign-up, which no two teams
    ever share. A name freed by a deletion can be taken again; a key
    cannot, so a key held across a deletion reaches nobody (`SignedOut`)
    rather than the team that took the name."""

    registered_at: float

    @property
    def dirname(self) -> str:
        """The name of the team's controllers directory."""
        return repr(self.registered_at)


@dataclass(frozen=True)
class Team:
    """A signed-up team, every version it pushed, and the source of the
    latest, which it will race next.

    A team signs up with no submission: it has no version and `source` is
    empty until its first accepted push, and until then it is not in the
    field.
    """

    name: str
    password_hash: str
    registered_at: float
    source: str = ""
    versions: tuple[Version, ...] = ()

    @property
    def key(self) -> TeamKey:
        return TeamKey(self.registered_at)

    @property
    def pushes(self) -> int:
        """The latest version's number: the accepted pushes so far."""
        return self.versions[-1].number if self.versions else 0

    @property
    def updated_at(self) -> float | None:
        """When the latest version was pushed."""
        return self.versions[-1].pushed_at if self.versions else None

    @property
    def submitted(self) -> bool:
        return bool(self.versions)

    def to_json(self) -> dict[str, Any]:
        """The team as the state file keeps it: everything but its source."""
        return {
            "name": self.name,
            "password_hash": self.password_hash,
            "registered_at": self.registered_at,
            "versions": [asdict(v) for v in self.versions],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any], source: str = "") -> "Team":
        """A team read back from the state file, with its latest source,
        which the file does not hold."""
        return cls(
            name=str(data["name"]),
            password_hash=str(data["password_hash"]),
            registered_at=float(data["registered_at"]),
            source=source,
            versions=tuple(Version.from_json(v) for v in data["versions"]),
        )


@dataclass(frozen=True)
class PushFeedback:
    """What the team's page says about its last push, accepted or not.

    `message` is a refusal's French headline; an accepted push has none,
    since its page just says OK. `analysis` is None when the push was
    refused before it was read (an empty or oversized file).
    `draft` is the refused source, so the page can hand it back for fixing
    rather than lose it, and `description` the description typed with it;
    an accepted push has neither.
    """

    at: float
    accepted: bool
    message: str
    analysis: Analysis | None = None
    draft: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class RacedCar:
    """One car of a race's grid, as the race recorded it: when its team
    signed up, which says whose car it was, and the number of the version it
    raced.

    Only the version's number is kept, so a description edited later shows
    wherever the version is named.
    """

    registered_at: float
    version: int


@dataclass(frozen=True)
class RaceReport:
    """A finished race, as the leaderboards and the admin page show it.

    `index` is the race's number in the event, stamped by
    `Store.record_race`; it is 0 until the race is recorded. The race's
    recording is not part of the report: the store keeps it apart
    (`Store.replay`), so reports stay small enough to copy around.

    `qualification_points` is, for an official race, each team's
    qualification score when the grid was seeded from it: qualifications go
    on between official races, so today's scores would no longer explain the
    grid.
    It is None for a qualification.

    `cars` is each car of the grid by name, snapshotted with the field and
    the sources it raced: a team is its sign-up, not its name, and a name
    freed by a deletion can be taken again, even while a race the old team
    is in runs.
    Whose car a name was is the store's to answer (`Store.team_races`).
    """

    record: RaceRecord
    track: str
    laps: int | None
    finished_at: float
    statuses: dict[str, str]
    faults: dict[str, str]
    cars: dict[str, RacedCar]
    void: bool = False
    void_reason: str | None = None
    index: int = 0
    qualification_points: dict[str, int] | None = None

    def to_json(self) -> dict[str, Any]:
        """The report as the state file keeps it."""
        return {
            "index": self.index,
            "kind": self.record.kind.value,
            "grid": list(self.record.grid),
            "classified": list(self.record.classified),
            "passes": None if self.record.passes is None else list(self.record.passes),
            "track": self.track,
            "laps": self.laps,
            "finished_at": self.finished_at,
            "statuses": self.statuses,
            "faults": self.faults,
            "void": self.void,
            "void_reason": self.void_reason,
            "qualification_points": self.qualification_points,
            "cars": {name: asdict(car) for name, car in self.cars.items()},
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RaceReport":
        """A report read back from the state file."""
        void_reason = data["void_reason"]
        points = data["qualification_points"]
        # Absent from a state file written before passes were counted.
        passes = data.get("passes")
        cars = {
            str(name): RacedCar(
                registered_at=float(car["registered_at"]), version=int(car["version"])
            )
            for name, car in data["cars"].items()
        }
        return cls(
            record=RaceRecord(
                kind=RaceKind(data["kind"]),
                grid=tuple(str(n) for n in data["grid"]),
                classified=tuple(str(n) for n in data["classified"]),
                passes=None if passes is None else tuple(int(n) for n in passes),
            ),
            track=str(data["track"]),
            laps=None if data["laps"] is None else int(data["laps"]),
            finished_at=float(data["finished_at"]),
            statuses={str(k): str(v) for k, v in data["statuses"].items()},
            faults={str(k): str(v) for k, v in data["faults"].items()},
            cars=cars,
            void=bool(data["void"]),
            void_reason=None if void_reason is None else str(void_reason),
            index=int(data["index"]),
            qualification_points=None
            if points is None
            else {str(k): int(v) for k, v in points.items()},
        )


@dataclass(frozen=True)
class TeamRace:
    """One race a team's own car ran, as its pages show it.

    `slot` is the car's place on the grid and `finish` its place in the
    classification, both from 1; `finish` is None for a void race or a car
    the race did not classify. `version` is the team's version the car
    raced. `status` is
    cocoracer's raw status and `fault` the car's fault, if it had one.
    """

    race: RaceReport
    slot: int
    finish: int | None
    status: str
    fault: str | None
    version: Version

    @property
    def gain(self) -> int | None:
        """What the race paid, signed and unfloored, or None with no finish:
        net passes in a qualification that counted them, places gained
        otherwise. Qualification scoring floors it
        (`scoring.qualification_points`)."""
        if self.finish is None:
            return None
        return race_gain(self.race.record, self.slot, self.finish)


@dataclass(frozen=True)
class VersionResults:
    """How one version did: the races it ran, qualifications and official
    races together, and its best finishing position, None if it was never
    classified. Void races count for nothing."""

    races: int
    best: int | None


def _kept(sent: str | None) -> str | None:
    """What a refused push sent, if there is anything in it to hand back."""
    return sent if sent and sent.strip() else None


def _write_atomically(path: Path, data: bytes) -> None:
    """Write `data` to `path` through a temp file beside it, fsync'd, then
    renamed over it, so a crash mid-write leaves the previous file."""
    directory = path.parent
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    # The rename itself reaches the disk only once the directory does.
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def validate_description(raw: str) -> str:
    """The canonical form of a version's description, or a rejection: one
    line, at most `MAX_DESCRIPTION_CHARS` characters, possibly empty."""
    description = " ".join(raw.split())
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise Rejected(
            f"La description dépasse {MAX_DESCRIPTION_CHARS} caractères.", 400
        )
    return description


def validate_name(raw: str) -> str:
    """The canonical form of a team name, or a rejection.

    Names are shown on a projector and used as race entrant names, so they
    are kept to plain characters rather than escaped downstream.
    """
    name = " ".join(raw.split())
    if not name:
        raise Rejected("Le nom d'équipe ne peut pas être vide.", 400)
    if len(name) > MAX_NAME_CHARS:
        raise Rejected(f"Le nom d'équipe dépasse {MAX_NAME_CHARS} caractères.", 400)
    if not _NAME_PATTERN.match(name):
        raise Rejected(
            "Le nom d'équipe ne peut contenir que des lettres sans accent, des "
            "chiffres, des espaces, - et _, et doit commencer par une lettre "
            "ou un chiffre.",
            400,
        )
    return name


def hash_password(password: str) -> str:
    """A salted scrypt hash of `password`, as `scrypt$n$r$p$salt$hash`.

    The string names its own parameters and salt (both hex), so it can be
    stored as is and checked by `password_matches` alone.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def password_matches(password: str, password_hash: str) -> bool:
    """Whether `password` hashes to `password_hash`, compared in constant time."""
    scheme, n, r, p, salt, digest = password_hash.split("$")
    if scheme != "scrypt":
        raise ValueError(f"unknown password hash scheme: {scheme!r}")
    supplied = _scrypt(password, bytes.fromhex(salt), int(n), int(r), int(p))
    return secrets.compare_digest(supplied, bytes.fromhex(digest))


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    # UTF-8, so a password with an accent in it hashes like any other.
    # `maxmem` doubles scrypt's 128 * r * n bytes, plus a megabyte for
    # OpenSSL's own overhead, which is all a tiny cost would need.
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=256 * r * n + 2**20,
    )


class Store:
    """Teams, submissions and race history for one event."""

    def __init__(
        self, max_teams: int = MAX_TEAMS, state_dir: Path | None = None
    ) -> None:
        """An empty store, or, given `state_dir`, the one saved there.

        With a state directory the store loads `state.json` if there is one,
        raising `StateError` if it cannot, then saves at once: a directory
        the server cannot write fails here, at startup, rather than at the
        first sign-up.
        """
        self._max_teams = max_teams
        # Every per-team map is keyed by the team's sign-up, as its
        # controllers directory is, never by its name.
        self._teams: dict[TeamKey, Team] = {}
        self._feedback: dict[TeamKey, PushFeedback] = {}
        self._races: list[RaceReport] = []
        # Replays by race index: the serialised bytes in memory, or, with a
        # state directory, None for one that is on disk.
        self._replays: dict[int, bytes | None] = {}
        # The cars' logs by race index, the same way.
        self._logs: dict[int, bytes | None] = {}
        self._signups_locked = False
        self._lock = threading.RLock()
        self._state_file: Path | None = None
        self._replay_dir: Path | None = None
        self._log_dir: Path | None = None
        self._controller_dir: Path | None = None
        # Without a state directory, every version's source by team and
        # number; with one, the sources are files and this stays empty.
        self._sources: dict[TeamKey, dict[int, str]] = {}
        self._saved_at: float | None = None
        self._restored_at: float | None = None
        self._save_error: str | None = None
        if state_dir is not None:
            self._state_file = state_dir / STATE_FILE
            self._replay_dir = state_dir / REPLAY_DIR
            self._replay_dir.mkdir(parents=True, exist_ok=True)
            self._log_dir = state_dir / LOG_DIR
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self._controller_dir = state_dir / CONTROLLER_DIR
            self._controller_dir.mkdir(parents=True, exist_ok=True)
            if self._state_file.exists():
                self._load(self._state_file)
                self._restored_at = time.time()
            self._write()

    @property
    def state_file(self) -> Path | None:
        """Where the state is saved, or None when it lives only in memory."""
        return self._state_file

    @property
    def saved_at(self) -> float | None:
        """When the state file was last written."""
        with self._lock:
            return self._saved_at

    @property
    def restored_at(self) -> float | None:
        """When this run loaded the state from disk; None if it started empty."""
        return self._restored_at

    @property
    def save_error(self) -> str | None:
        """Why the last save failed, or None if it succeeded."""
        with self._lock:
            return self._save_error

    def _load(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            version = data.get("version") if isinstance(data, dict) else None
            if version != STATE_VERSION:
                raise StateError(
                    f"{path}: unknown state version {version!r} "
                    f"(this server reads version {STATE_VERSION})"
                )
            teams = [self._team_from_json(t) for t in data["teams"]]
            races = [RaceReport.from_json(r) for r in data["races"]]
            locked = data["signups_locked"]
            if not isinstance(locked, bool):
                raise TypeError(f"'signups_locked' is {locked!r}, not a boolean")
        except StateError:
            raise
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise StateError(f"{path}: cannot load the saved state: {exc}") from exc
        self._teams = {t.key: t for t in teams}
        self._races = races
        self._signups_locked = locked
        # A replay or log file with no recorded race behind it (a crash
        # between the writes) is ignored, and overwritten by the next race.
        assert self._replay_dir is not None
        self._replays = {
            r.index: None
            for r in races
            if not r.void and self._replay_path(r.index).exists()
        }
        self._logs = {
            r.index: None
            for r in races
            if not r.void and self._log_path(r.index).exists()
        }

    def _team_from_json(self, data: dict[str, Any]) -> Team:
        team = Team.from_json(data)
        if not team.versions:
            return team
        latest = self._source_path(team, team.pushes)
        return replace(team, source=latest.read_text(encoding="utf-8"))

    def _source_path(self, team: Team, number: int) -> Path:
        return self._team_dir(team) / f"{number}.py"

    def _team_dir(self, team: Team) -> Path:
        assert self._controller_dir is not None
        return self._controller_dir / team.key.dirname

    def _replay_path(self, index: int) -> Path:
        assert self._replay_dir is not None
        return self._replay_dir / f"{index}.json"

    def _log_path(self, index: int) -> Path:
        assert self._log_dir is not None
        return self._log_dir / f"{index}.json"

    def _save(self) -> None:
        """Rewrite the state file after a mutation; called under the lock.

        A failed save does not undo the mutation or fail the request that
        made it: the event goes on in memory, the error is logged, and the
        admin page shows it until a later save succeeds.
        """
        if self._state_file is None:
            return
        try:
            self._write()
        except OSError as exc:
            self._failed_save(self._state_file, exc)

    def _failed_save(self, path: Path, exc: OSError) -> None:
        self._save_error = f"{type(exc).__name__}: {exc}"
        print(
            f"cocoracer-server: cannot save {path}: {exc}", file=sys.stderr, flush=True
        )

    def _write(self) -> None:
        """Write the state file atomically."""
        assert self._state_file is not None
        state = {
            "version": STATE_VERSION,
            "signups_locked": self._signups_locked,
            "teams": [t.to_json() for t in self._teams.values()],
            "races": [r.to_json() for r in self._races],
        }
        data = json.dumps(state, ensure_ascii=False).encode("utf-8")
        _write_atomically(self._state_file, data)
        self._saved_at = time.time()
        self._save_error = None

    @property
    def signups_locked(self) -> bool:
        """True while the admin refuses new teams; pushes are unaffected."""
        with self._lock:
            return self._signups_locked

    def lock_signups(self) -> None:
        with self._lock:
            if not self._signups_locked:
                self._signups_locked = True
                self._save()

    def unlock_signups(self) -> None:
        with self._lock:
            if self._signups_locked:
                self._signups_locked = False
                self._save()

    def register(self, raw_name: str, password: str) -> Team:
        """Sign a team up, with no submission yet; returns it, under its
        canonical name.

        No two signed-up teams share a name, so a taken one is refused. A
        name a deleted team freed is not taken: the team that claims it
        starts clean (`team_races`). The cap counts every signed-up team,
        submission or not: a seat is claimed at sign-up. While the admin
        has locked sign-ups, every new team is refused.
        """
        name = validate_name(raw_name)
        if not password:
            raise Rejected("Choisis un mot de passe.", 400)
        # Hashed before the lock is taken: it is the slow part.
        password_hash = hash_password(password)
        with self._lock:
            if self._signups_locked:
                raise Rejected(
                    "Les inscriptions sont fermées : l'événement n'accepte plus "
                    "de nouvelle équipe.",
                    409,
                )
            if self._named(name) is not None:
                raise Rejected(
                    f"Le nom « {name} » est déjà pris. Connecte-toi si "
                    f"c'est ton équipe, sinon choisis-en un autre.",
                    409,
                )
            if len(self._teams) >= self._max_teams:
                raise Rejected(
                    f"L'événement est complet : {self._max_teams} équipes "
                    f"sont déjà inscrites.",
                    409,
                )
            # The sign-up time is the team's key (`TeamKey`), so no two
            # teams may share one.
            now = time.time()
            while TeamKey(now) in self._teams:
                now = math.nextafter(now, math.inf)
            team = Team(name=name, password_hash=password_hash, registered_at=now)
            self._teams[team.key] = team
            self._save()
            return team

    def authenticate(self, raw_name: str, password: str) -> Team | None:
        """The team these credentials sign in as, or None.

        A malformed name is just a failed sign-in: the page says the same
        thing for every wrong pair, so it never confirms which half was
        wrong.
        """
        name = " ".join(raw_name.split())
        with self._lock:
            team = self._named(name)
        if (
            team is None
            or not password
            or not password_matches(password, team.password_hash)
        ):
            return None
        return team

    def push(self, key: TeamKey, source: str, description: str = "") -> Analysis:
        """Add a signed-up team's next version, if it passes the checks.

        A key whose team is gone raises `SignedOut`, and nothing is kept for
        whichever team has its name now. The source is
        read by static analysis, never run: errors refuse the push and the
        previous version keeps racing, warnings are kept for the page and
        the push goes through. A description over the limit refuses it too.
        A refused push uses up no number. Either way the outcome is kept as
        the team's `feedback`, which is not saved: only an accepted push
        rewrites the state file.
        """
        with self._lock:
            team = self._live(key)
            try:
                try:
                    described = validate_description(description)
                except Rejected as exc:
                    raise PushRejected(exc.detail, exc.status_code) from exc
                if not source.strip():
                    raise PushRejected("Le contrôleur est vide.", 400)
                if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
                    raise PushRejected(TOO_LARGE, 413)
                analysis = check_source(source)
                if not analysis.ok:
                    raise PushRejected(
                        "Le contrôleur contient des erreurs.", 422, analysis
                    )
                version = Version(team.pushes + 1, described, time.time())
                self._keep_source(team, version.number, source)
            except PushRejected as exc:
                self.refuse(key, exc.detail, exc.analysis, source, description)
                raise
            self._teams[key] = replace(
                team, source=source, versions=(*team.versions, version)
            )
            self._save()
            self._feedback[key] = PushFeedback(
                at=version.pushed_at,
                accepted=True,
                message="",
                analysis=analysis,
            )
            return analysis

    def _keep_source(self, team: Team, number: int, source: str) -> None:
        """Keep a version's source: in memory, or written to its file. A
        file that cannot be written refuses the push, since the state file
        would otherwise name a version a restart cannot find."""
        if self._controller_dir is None:
            self._sources.setdefault(team.key, {})[number] = source
            return
        path = self._source_path(team, number)
        try:
            path.parent.mkdir(exist_ok=True)
            _write_atomically(path, source.encode("utf-8"))
        except OSError as exc:
            self._failed_save(path, exc)
            raise PushRejected(
                "Le serveur n'a pas pu enregistrer le contrôleur ; réessaie.", 500
            ) from exc

    def describe(self, key: TeamKey, number: int, description: str) -> None:
        """Change the description of one of a team's versions; its source,
        and so what races, stays as it is. `SignedOut` if the team is gone."""
        described = validate_description(description)
        with self._lock:
            team = self._live(key)
            if not any(v.number == number for v in team.versions):
                raise Rejected("Version inconnue.", 404)
            versions = tuple(
                replace(v, description=described) if v.number == number else v
                for v in team.versions
            )
            self._teams[key] = replace(team, versions=versions)
            self._save()

    def version_source(self, key: TeamKey, number: int) -> str | None:
        """The source of a team's version `number`, or None: no such
        version, or its file could not be read. `SignedOut` if the team is
        gone."""
        with self._lock:
            team = self._live(key)
            if not any(v.number == number for v in team.versions):
                return None
            if number == team.pushes:
                return team.source
            if self._controller_dir is None:
                return self._sources[key][number]
            path = self._source_path(team, number)
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            print(
                f"cocoracer-server: cannot read {path}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            return None

    def refuse(
        self,
        key: TeamKey,
        detail: str,
        analysis: Analysis | None = None,
        draft: str | None = None,
        description: str | None = None,
    ) -> None:
        """Keep a refused push as the team's feedback; the submission stays.

        `push` calls this for what it refuses; the HTTP layer calls it for
        what only it can see, such as a file that is not text. `draft` and
        `description` are what the team sent, for the page to hand back.
        `SignedOut` if the team is gone.
        """
        with self._lock:
            team = self._live(key)
            kept = (
                "ta soumission précédente continue de courir"
                if team.submitted
                else "ton équipe n'a toujours pas de soumission"
            )
            self._feedback[key] = PushFeedback(
                at=time.time(),
                accepted=False,
                message=f"Envoi refusé, {kept}. {detail}",
                analysis=analysis,
                draft=_kept(draft),
                description=_kept(description),
            )

    def feedback(self, key: TeamKey) -> PushFeedback | None:
        """The outcome of the team's last push, for its page."""
        with self._lock:
            return self._feedback.get(key)

    def delete(self, name: str) -> bool:
        """Remove a team and every version it pushed; False if there was no
        such team.

        Its past race results stay in the history — rewriting finished races
        would change other teams' scores — but it is gone from the field and
        from the leaderboards. A team that takes its name later starts
        clean: those races are not its (`team_races`).
        """
        with self._lock:
            team = self._named(name)
            if team is None:
                return False
            del self._teams[team.key]
            self._feedback.pop(team.key, None)
            self._sources.pop(team.key, None)
            self._save()
            if self._controller_dir is not None:
                # After the save: a team the state file still names keeps
                # its sources. A directory left behind is harmless, since
                # no later team can have the same key.
                try:
                    shutil.rmtree(self._team_dir(team))
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    print(
                        f"cocoracer-server: cannot remove {self._team_dir(team)}: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
            return True

    def account(self, name: str) -> Team | None:
        """The signed-up team that has this name now, submission or not."""
        with self._lock:
            return self._named(name)

    def team(self, key: TeamKey) -> Team | None:
        """The team signed up under `key`, or None if it is gone."""
        with self._lock:
            return self._teams.get(key)

    def _named(self, name: str) -> Team | None:
        return next((t for t in self._teams.values() if t.name == name), None)

    def _live(self, key: TeamKey) -> Team:
        """The team signed up under `key`; `SignedOut` if it is gone.
        Called under the lock, so the action that follows is the team's."""
        team = self._teams.get(key)
        if team is None:
            raise SignedOut()
        return team

    def accounts(self) -> list[Team]:
        """Every signed-up team, by name, including those not in the field."""
        with self._lock:
            return sorted(self._teams.values(), key=lambda t: t.name)

    def teams(self) -> list[Team]:
        """The field: every team with a submission, by name.

        `Team` is frozen and the list is rebuilt on each call, so the result
        is a snapshot: a race started from it is unaffected by a push that
        lands while it runs.
        """
        with self._lock:
            return [t for t in self.accounts() if t.submitted]

    def record_race(
        self,
        report: RaceReport,
        replay: bytes | None = None,
        logs: dict[str, Any] | None = None,
    ) -> RaceReport:
        """Number the race and keep it, returning the numbered report.

        The number is assigned here rather than read first and written
        after, so two races finishing together cannot claim the same one.
        `replay` is the race's recording, already serialised; a void race
        keeps none. With a state directory it is written to its own file
        before the state file names the race, and only the fact that it
        exists stays in memory. A replay that cannot be written is logged
        like a failed save, and the race is kept without one. `logs`, what
        each car printed by team, is kept the same way, beside the replay.
        """
        with self._lock:
            numbered = replace(report, index=len(self._races) + 1)
            if replay is not None and not numbered.void:
                self._keep_replay(numbered.index, replay)
            if logs and not numbered.void:
                self._keep_logs(numbered.index, logs)
            self._races.append(numbered)
            self._save()
            return numbered

    def _keep_replay(self, index: int, replay: bytes) -> None:
        if self._replay_dir is None:
            self._replays[index] = replay
            return
        path = self._replay_path(index)
        try:
            _write_atomically(path, replay)
        except OSError as exc:
            self._failed_save(path, exc)
            return
        self._replays[index] = None

    def _keep_logs(self, index: int, logs: dict[str, Any]) -> None:
        data = json.dumps(logs, ensure_ascii=False, separators=(",", ":")).encode()
        if self._log_dir is None:
            self._logs[index] = data
            return
        path = self._log_path(index)
        try:
            _write_atomically(path, data)
        except OSError as exc:
            self._failed_save(path, exc)
            return
        self._logs[index] = None

    def log(self, index: int, key: TeamKey) -> dict[str, Any] | None:
        """What the team's car printed in race `index`, as the race worker
        wrote it (`{"lines": [[t, text], ...], "truncated"}`), or None: it
        printed nothing, the race had no car of the team's, the team is gone,
        or the logs could not be read. A name taken again after a deletion
        never reads the old team's log.
        """
        with self._lock:
            owned = self._owned_race(key, index)
            if owned is None or index not in self._logs:
                return None
            team, _ = owned
            kept = self._logs[index]
        try:
            data = self._log_path(index).read_bytes() if kept is None else kept
            logs: dict[str, Any] = json.loads(data)
        except (OSError, ValueError) as exc:
            print(
                f"cocoracer-server: cannot read the logs of race {index}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            return None
        return logs.get(team.name)

    def replay(self, index: int) -> bytes | Path | None:
        """The recording of race `index`: its bytes when the store is in
        memory, its file when it is on disk, None when it has none (a void
        race, an unknown one, or one whose file could not be written)."""
        with self._lock:
            if index not in self._replays:
                return None
            kept = self._replays[index]
            return self._replay_path(index) if kept is None else kept

    def races(self) -> list[RaceReport]:
        with self._lock:
            return list(self._races)

    def race(self, index: int) -> RaceReport | None:
        """The race numbered `index`, or None if there is none."""
        with self._lock:
            if 1 <= index <= len(self._races):
                return self._races[index - 1]
            return None

    def team_races(self, key: TeamKey) -> list[TeamRace]:
        """Every race the team's own car ran, oldest first, void ones
        included.

        Not the races of a deleted team whose name it took, and nothing at
        all for a team that is no longer signed up. The versions are read as
        the store has them now, so an edited description shows.
        """
        with self._lock:
            team = self._teams.get(key)
            if team is None:
                return []
            return [
                self._team_race(r, team) for r in self._races if self._owns(r, team)
            ]

    def team_race(self, key: TeamKey, index: int) -> TeamRace | None:
        """Race `index`, if the team's own car ran it; None otherwise."""
        with self._lock:
            owned = self._owned_race(key, index)
            if owned is None:
                return None
            team, report = owned
            return self._team_race(report, team)

    def _owned_race(self, key: TeamKey, index: int) -> tuple[Team, RaceReport] | None:
        """The team signed up under `key` and race `index`, if the team's
        own car ran it. Called under the lock."""
        team = self._teams.get(key)
        report = self.race(index)
        if team is None or report is None or not self._owns(report, team):
            return None
        return team, report

    def version_results(self, key: TeamKey) -> dict[int, VersionResults]:
        """How each of the team's versions that raced did, by number."""
        results: dict[int, VersionResults] = {}
        for raced in self.team_races(key):
            if raced.race.void:
                continue
            number = raced.version.number
            so_far = results.get(number, VersionResults(0, None))
            best = so_far.best
            if raced.finish is not None:
                best = raced.finish if best is None else min(best, raced.finish)
            results[number] = VersionResults(so_far.races + 1, best)
        return results

    @staticmethod
    def _owns(report: RaceReport, team: Team) -> bool:
        """Whether one of the race's cars is `team`'s, rather than a deleted
        team's that had its name.

        The one test of whether a car is a team's: everything that shows or
        counts a team's races goes through it. The car is the team's if it
        was entered under the team's sign-up time.
        """
        car = report.cars.get(team.name)
        return car is not None and car.registered_at == team.registered_at

    @staticmethod
    def _team_race(report: RaceReport, team: Team) -> TeamRace:
        name = team.name
        classified = report.record.classified
        finish = None
        if not report.void and name in classified:
            finish = classified.index(name) + 1
        return TeamRace(
            race=report,
            slot=report.record.grid.index(name) + 1,
            finish=finish,
            status=report.statuses.get(name, ""),
            fault=report.faults.get(name),
            version=_raced_version(report, team),
        )

    def scoring_records(self) -> list[RaceRecord]:
        """The finished, non-void races, as the scoring functions want them.

        Each car is entered under its team's name only if it is that team's
        (`_owns`). A deleted team's car, its name since taken or
        not, is entered under a key no team can have: it is not dropped,
        since its presence still shaped the positions the other cars gained
        and the size of an official field, but it counts for nobody now.
        """
        with self._lock:
            by_name = {t.name: t for t in self._teams.values()}
            return [self._scored(r, by_name) for r in self._races if not r.void]

    def _scored(self, report: RaceReport, by_name: dict[str, Team]) -> RaceRecord:
        def entrant(name: str) -> str:
            team = by_name.get(name)
            if team is not None and self._owns(report, team):
                return name
            # A team name starts with a letter or a digit, never "#".
            return f"#{report.index} {name}"

        record = report.record
        return replace(
            record,
            grid=tuple(entrant(n) for n in record.grid),
            classified=tuple(entrant(n) for n in record.classified),
        )

    def standings(self) -> list[Standing]:
        """Current standings: every team in the field, best first.

        The field and the race history are read under one lock, so a
        deletion cannot land between them and leave a team on the board with
        nothing behind it.
        """
        with self._lock:
            field = [t.name for t in self.teams()]
            return final_standings(self.scoring_records(), field)


def _raced_version(report: RaceReport, team: Team) -> Version:
    """The team's version its car raced in `report`, a race the team owns
    (`Store._owns`). A number the team no longer has (it cannot happen:
    versions go only with the team) reads as its bare label.
    """
    number = report.cars[team.name].version
    for version in team.versions:
        if version.number == number:
            return version
    return Version(number, "", report.finished_at)
