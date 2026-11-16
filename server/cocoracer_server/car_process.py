"""Each car's controller in its own process, behind a pipe (ADR 0007): the
car's side, and what the car and the worker share.

The race worker forks one child per car before any team's source reaches
it. The child (`car_main`) sandboxes itself, then answers one call at a
time on its pipe: the import of its team's file, `reset`, and every `step`.
The worker's side, its handle on each child and the adapter that steps the
field through cocoracer's seam, is `car_field`.

Inside its process a car:

- sets `oom_score_adj` to 1000 (`OOM_SCORE_ADJ`), so that should the unit
  run out of memory anyway the kernel kills a car before the worker;
- caps its address space at its size after the imports plus
  `MEMORY_BUDGET_MB`, so an allocation past the budget raises
  `MemoryError` in the controller that asked for it (`MEMORY_FAULT`);
- installs the seccomp filter (`sandbox`), and refuses to load its team's
  file if that failed (`SANDBOX_FAULT`; the cause goes to the worker's
  stderr, for the admin);
- makes itself non-dumpable, so no sibling can open its `/proc` files
  (its pipes, its memory), and dies with the worker (`PR_SET_PDEATHSIG`);
- installs a Landlock ruleset: no file written anywhere, none read but
  the libraries a later import needs (`sandbox`);
- imports its team's file from memory: the file the import names is a
  symlink to `/proc/self/fd/5`, which resolves to this car's own copy in
  this process and to nothing in any other, and the import and its
  tracebacks are served the source without reading it;
- judges what its controller returns, and words what it raised, by
  cocoracer's own rule (`validate_command`, `fault_text`);
- keeps what the controller prints, with `Log`'s rules, and hands it back
  with each reply.

The limits and faults below are shared: the worker enforces them
(`call_limit`, read at call time so a test can shorten one).
"""

import fcntl
import gc
import io
import json
import linecache
import os
import pickle
import resource
import signal
import struct
import sys
import traceback
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any, NoReturn

from cocoracer.controller import (
    Controller,
    ControllerError,
    load_controller,
)
from cocoracer.field import FAULT_CHARS, fault_text, validate_command

from cocoracer_server import sandbox

# How much memory one car may take on top of its process's size after the
# imports, its own import included, enforced as that car's RLIMIT_AS. The
# start kit's wtf.md quotes this number to teams; change both together.
MEMORY_BUDGET_MB = 512

# The fault a team reads when its car goes over `MEMORY_BUDGET_MB`.
MEMORY_FAULT = f"le contrôleur a utilisé plus de {MEMORY_BUDGET_MB} Mo de mémoire"

# Seconds one `step()` may take before its car is faulted: a deadline on
# reading the car's reply, after which its process is killed. The start
# kit's wtf.md quotes this number to teams; change them together.
STEP_TIME_LIMIT = 1.0
STEP_TIMEOUT_FAULT = f"le contrôleur n'a pas répondu en {STEP_TIME_LIMIT:g} s"

# On top of the limit above, what one car's `step()` may take on average
# over its last `STEP_AVERAGE_WINDOW` steps (4 s of race at 40 Hz), counted
# only while it is stepped and judged once it has that many. 20 ms is the
# 25 ms tick less the engine's ~4 ms and a little for the pipe. Wall-clock,
# like the limit, so not reproducible bit for bit. The start kit's wtf.md
# quotes it; change them together.
STEP_AVERAGE_LIMIT = 0.020
STEP_AVERAGE_WINDOW = 160
STEP_AVERAGE_FAULT = (
    f"le contrôleur a pris en moyenne plus de {STEP_AVERAGE_LIMIT * 1000:g} ms par step"
)

# Seconds `reset()` may take, once, before the countdown: generous, because
# it is where a controller warms up (imports, JIT, a first inference).
RESET_TIME_LIMIT = 5.0
RESET_TIMEOUT_FAULT = (
    f"le contrôleur n'a pas répondu en {RESET_TIME_LIMIT:g} s dans reset()"
)

# Seconds the import of a team's file (its module body) may take.
LOAD_TIME_LIMIT = 10.0
LOAD_TIMEOUT_FAULT = f"le contrôleur n'a pas été chargé en {LOAD_TIME_LIMIT:g} s"

# The prefix of the fault a team reads when its file would not import.
LOAD_FAULT = "le contrôleur n'a pas pu être chargé"

# When the car's process stopped answering: it exited, was killed, or
# closed its pipe. A detail in parentheses says which, when it is known.
STOPPED_FAULT = "le processus du contrôleur s'est arrêté"

# When what came back on the pipe is not a reply the worker can read.
GARBLED_FAULT = "le processus du contrôleur a renvoyé une réponse illisible"

# When the car's process could not install its seccomp filter (or start at
# all). Nothing else keeps a car off the network, so it never runs.
SANDBOX_FAULT = "le serveur n'a pas pu isoler le contrôleur, qui n'a pas couru"

# How much one car may print in one race, in characters, before the rest of
# its log is dropped: a line on every step of a five-minute official race
# fits, a print in a runaway loop does not fill the disk. The start kit's
# wtf.md quotes this number to teams; change both together.
LOG_BUDGET_CHARS = 512 * 1024

# A longer line is cut to this many characters.
LOG_LINE_CHARS = 1000

# Written to a car's `/proc/self/oom_score_adj`: the most killable a process
# can be, above the worker's own score.
OOM_SCORE_ADJ = 1000

# The descriptors a car reads its calls from and writes its replies to, and
# the one that holds its team's source, which the file its import names (a
# symlink to `/proc/self/fd/5`, made by the worker) resolves to in that car's
# process alone.
CALLS_FD = 3
REPLIES_FD = 4
SOURCE_FD = 5

# What a reply may carry on top of its log lines, in bytes: the command, a
# fault's text, the JSON around them. Each log character may take up to
# six bytes of JSON (`\u001b`), and each line three more around it.
REPLY_OVERHEAD = 16 * 1024

# How long, once a car's pipe closes, the worker waits for its exit status
# before killing it.
EXIT_GRACE = 0.5

FRAME = struct.Struct(">I")


class Log:
    """What one car printed, line by line, each line stamped with the race
    time of the call that printed it.

    Text is gathered into lines as it is written, and a call that ends
    partway through a line ends the line. Past `budget` characters the log
    takes nothing more and is marked truncated.

    A car's process keeps one to gather what its controller prints (every
    stamp 0, it has no clock); the worker keeps one per car, fed the lines
    each reply brings with `add`, which stamps them and checks them against
    the same rules.
    """

    def __init__(self, budget: int = LOG_BUDGET_CHARS) -> None:
        self.lines: list[tuple[float, str]] = []
        self.truncated = False
        self._left = budget
        self._partial = ""
        self._taken = 0

    def write(self, text: str) -> None:
        if self.truncated:
            return
        *complete, rest = text.split("\n")
        for part in complete:
            self._emit(self._partial + part)
            self._partial = ""
        # Bounded, so a call that never writes a newline cannot grow it.
        self._partial = (self._partial + rest)[: LOG_LINE_CHARS + 1]

    def end(self) -> None:
        if self._partial:
            self._emit(self._partial)
            self._partial = ""

    def _emit(self, line: str) -> None:
        if self.truncated:
            return
        line = line.rstrip("\r")
        if len(line) > LOG_LINE_CHARS:
            line = line[:LOG_LINE_CHARS] + "…"
        self._left -= len(line) + 1
        if self._left < 0:
            self.truncated = True
            return
        self.lines.append((0.0, line))

    def take(self) -> list[str]:
        """The lines emitted since the last `take`, without their stamps."""
        taken = [text for _, text in self.lines[self._taken :]]
        self._taken = len(self.lines)
        return taken

    @property
    def left(self) -> int:
        return max(0, self._left)

    def add(self, t: float, lines: object, truncated: object) -> bool:
        """Append a reply's lines, stamped `t`; False if they break the rules
        a car's own `Log` keeps, which only a forged reply does."""
        if not isinstance(lines, list) or not isinstance(truncated, bool):
            return False
        stamp = round(float(t), 3)
        for line in lines:
            if not isinstance(line, str) or len(line) > LOG_LINE_CHARS + 1:
                return False
            self._left -= len(line) + 1
            if self._left < 0:
                return False
            self.lines.append((stamp, line))
        self.truncated = self.truncated or truncated
        return True

    def to_json(self) -> dict[str, Any]:
        return {
            "lines": [[t, text] for t, text in self.lines],
            "truncated": self.truncated,
        }


# --- the car's side: the child process ------------------------------------


class _Printed(io.TextIOBase):
    """`sys.stdout` and `sys.stderr` in a car's process: everything the
    controller writes, from any thread, goes to the car's `Log`."""

    def __init__(self, log: Log) -> None:
        self._log = log

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return "utf-8"

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def write(self, text: str) -> int:
        self._log.write(str(text))
        return len(text)

    def flush(self) -> None:
        pass


def _address_space() -> int:
    """This process's address space, in bytes."""
    with open("/proc/self/statm") as f:
        return int(f.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")


def _cap_address_space() -> None:
    """Let this car grow by `MEMORY_BUDGET_MB` beyond its size now, and no
    further: soft and hard limit both, so the controller cannot lift it."""
    limit = _address_space() + MEMORY_BUDGET_MB * 1024 * 1024
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    if hard != resource.RLIM_INFINITY:
        limit = min(limit, hard)
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def volunteer_for_the_oom_killer(score: int) -> None:
    """Write `score` to this process's `oom_score_adj`, so the kernel picks
    it, when memory runs out, ahead of every process that scores lower.

    Setting one's own score above the one inherited needs no privilege; off
    Linux, or where /proc is not writable, there is nothing to do and the
    process runs anyway.
    """
    try:
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write(str(score))
    except OSError:
        pass


def _take_descriptors(calls: int, replies: int) -> None:
    """Keep the two ends of this car's pipe as `CALLS_FD` and `REPLIES_FD`,
    point stdin, stdout and stderr at /dev/null, and close everything else:
    the worker's own descriptors and the other cars' pipes."""
    high_calls = fcntl.fcntl(calls, fcntl.F_DUPFD, 10)
    high_replies = fcntl.fcntl(replies, fcntl.F_DUPFD, 10)
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    os.dup2(high_calls, CALLS_FD)
    os.dup2(high_replies, REPLIES_FD)
    os.closerange(REPLIES_FD + 1, os.sysconf("SC_OPEN_MAX"))


def _read_exactly(fd: int, n: int) -> bytes | None:
    data = bytearray()
    while len(data) < n:
        chunk = os.read(fd, n - len(data))
        if not chunk:
            return None
        data += chunk
    return bytes(data)


def _read_call(fd: int) -> tuple[str, Any] | None:
    header = _read_exactly(fd, FRAME.size)
    if header is None:
        return None
    payload = _read_exactly(fd, FRAME.unpack(header)[0])
    if payload is None:
        return None
    call: tuple[str, Any] = pickle.loads(payload)  # from the worker: trusted
    return call


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _reply(fd: int, reply: dict[str, Any]) -> None:
    payload = json.dumps(reply, ensure_ascii=False).encode()
    _write_all(fd, FRAME.pack(len(payload)) + payload)


# The worker's own files, whose frames a traceback for the team leaves out.
_OWN_FILES = frozenset({__file__, sys.modules[load_controller.__module__].__file__})


def _team_traceback(exc: BaseException) -> str:
    """The traceback of what the controller raised, without the frames of
    the code that called it."""
    chain = traceback.TracebackException.from_exception(exc)
    seen: traceback.TracebackException | None = chain
    while seen is not None:
        seen.stack = traceback.StackSummary.from_list(
            [
                frame
                for frame in seen.stack
                if frame.filename not in _OWN_FILES
                and not frame.filename.startswith("<frozen importlib")
            ]
        )
        seen = seen.__cause__ or seen.__context__
    return "".join(chain.format())


class _CarSide:
    """What a car's process keeps between calls."""

    def __init__(self, sandbox_failure: str | None) -> None:
        self.sandbox_failure = sandbox_failure
        self.controller: Controller | None = None
        self.log = Log()

    def handle(self, kind: str, args: Any) -> dict[str, Any]:
        try:
            if kind == "load":
                return self._load(*args)
            if self.controller is None:
                return {"e": "aucun contrôleur chargé"}
            if kind == "reset":
                self.controller.reset(args)
                return {}
            command = self.controller.step(*args)
            speed, steering = validate_command(command)
            return {"c": [speed, steering]}
        except MemoryError:
            # Outside the handler the hog's frames are released; the reply
            # itself needs little.
            pass
        except Exception as exc:
            self.log.write(_team_traceback(exc))
            return {"e": fault_text(exc)}
        self.controller = None
        gc.collect()
        return {"m": 1}

    def _load(
        self, source: str, path: str, baselines: dict[str, Any]
    ) -> dict[str, Any]:
        if self.sandbox_failure is not None:
            return {"s": self.sandbox_failure}
        link = Path(path)
        data = source.encode("utf-8")
        memfd = os.memfd_create(link.name)
        _write_all(memfd, data)
        if memfd != SOURCE_FD:
            os.dup2(memfd, SOURCE_FD)
            os.close(memfd)
        _serve_from_memory(str(link), data)
        try:
            self.controller = load_controller(link, baselines=baselines)
        except ControllerError as exc:
            if isinstance(exc.__cause__, MemoryError):
                raise MemoryError from None
            self.log.write(_team_traceback(exc.__cause__ or exc))
            return {"e": f"{LOAD_FAULT} : {exc}"[:FAULT_CHARS]}
        except MemoryError:
            raise
        except Exception as exc:  # a module body can raise anything at all
            self.log.write(_team_traceback(exc))
            return {"e": f"{LOAD_FAULT} : {type(exc).__name__}: {exc}"[:FAULT_CHARS]}
        return {}


def _serve_from_memory(path: str, data: bytes) -> None:
    """Have the import, and every traceback, read the team's file at `path`
    from `data` rather than from disk, where the sandbox lets the car read
    nothing of its own: Python's source loader is patched for that one path
    (this process imports nothing else of the kind), and the source is put
    in `linecache` as a loader's would be, which it never checks on disk."""
    original = SourceFileLoader.get_data

    def get_data(self: SourceFileLoader, wanted: str) -> bytes:
        if wanted == path:
            return data
        return original(self, wanted)

    SourceFileLoader.get_data = get_data  # type: ignore[method-assign,assignment]
    text = data.decode("utf-8")
    linecache.cache[path] = (len(text), None, text.splitlines(True), path)


def _serve(parent: int) -> None:
    """Set the car's process up, then answer calls until the pipe closes."""
    sandbox.prctl(sandbox.PR_SET_PDEATHSIG, signal.SIGKILL)  # die with the worker
    if os.getppid() != parent:  # it died before that took
        return
    volunteer_for_the_oom_killer(OOM_SCORE_ADJ)
    _cap_address_space()
    sys.dont_write_bytecode = True  # no .pyc of a team's file on disk
    failure: str | None = None
    try:
        readable = sandbox.library_paths()
        sandbox.install()
        sandbox.restrict_files(readable)
    except BaseException as exc:
        failure = f"{type(exc).__name__}: {exc}"
    # After the OOM score, which a non-dumpable process can no longer write.
    sandbox.prctl(sandbox.PR_SET_DUMPABLE, 0)
    side = _CarSide(failure)
    sys.stdout = sys.stderr = _Printed(side.log)
    # Ready: the worker waits for every car's hello before it reads the
    # first source.
    hello: dict[str, Any] = {"o": [], "t": False}
    if failure is not None:
        hello["s"] = failure
    _reply(REPLIES_FD, hello)
    while (call := _read_call(CALLS_FD)) is not None:
        kind, args = call
        reply = side.handle(kind, args)
        side.log.end()
        reply["o"] = side.log.take()
        reply["t"] = side.log.truncated
        try:
            _reply(REPLIES_FD, reply)
        except MemoryError:
            _reply(REPLIES_FD, {"m": 1, "o": [], "t": side.log.truncated})


def car_main(calls: int, replies: int, parent: int) -> NoReturn:
    """The forked child's whole life. It never returns into the worker's
    code: whatever happens, it ends in `os._exit`."""
    code = 1
    try:
        _take_descriptors(calls, replies)
        _serve(parent)
        code = 0
    except SystemExit as exc:  # the controller's own sys.exit()
        code = exc.code if isinstance(exc.code, int) else 1
    except BaseException:
        code = 1
    finally:
        os._exit(code)


def call_limit(kind: str) -> tuple[float, str]:
    """A call of `kind`'s time limit, and the fault when it is overrun.

    The constants are read at call time, so a test can shorten a limit with
    `monkeypatch.setattr`.
    """
    match kind:
        case "hello":
            return LOAD_TIME_LIMIT, SANDBOX_FAULT
        case "load":
            return LOAD_TIME_LIMIT, LOAD_TIMEOUT_FAULT
        case "reset":
            return RESET_TIME_LIMIT, RESET_TIMEOUT_FAULT
        case "step":
            return STEP_TIME_LIMIT, STEP_TIMEOUT_FAULT
    raise ValueError(f"no such call: {kind}")
