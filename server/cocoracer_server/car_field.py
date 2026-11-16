"""The worker's side of the cars (ADR 0007), behind cocoracer's field seam.

The worker keeps a `Car` per child process (`car_process`), which sends
each call's arguments down the pipe and reads the reply back, and races
them as a `CarField`: the adapter cocoracer's engine steps its field
through (`cocoracer.field`). The engine judges the commands, applies the
fault rule and applies the commands in grid order, as for controllers in
its own process; a race comes out the same.

Whatever a controller does in its own process, the worker sees one of four
things: a reply, a reply it cannot read, a closed pipe, or no reply by the
call's deadline. Anything but a good reply DNFs the car with a French
reason and kills its process. `sys.exit`, `os._exit`, a segfault, a stolen
signal handler or a C loop that never yields all come down to the last
three. The rest of the field races on.

The field is stepped in parallel (`CarField`, `collect`): every running car
is sent its `step` arguments, then every reply is read, each against its
own deadline counted from its send, so a tick costs the slowest car rather
than the sum of all of them, and the race stays deterministic but for the
wall-clock faults. A car whose `step()` averages more than
`STEP_AVERAGE_LIMIT` over its last `STEP_AVERAGE_WINDOW` steps is DNF'd
too: one slow car would otherwise hold the whole live race back.
"""

import gc
import json
import math
import os
import pickle
import select
import signal
import sys
import time
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from cocoracer.controller import TrackInfo
from cocoracer.field import FAULT_CHARS, ControllerFault, Fault, Observation

from cocoracer_server import car_process
from cocoracer_server.car_process import (
    EXIT_GRACE,
    FRAME,
    GARBLED_FAULT,
    MEMORY_FAULT,
    REPLY_OVERHEAD,
    SANDBOX_FAULT,
    SOURCE_FD,
    STEP_AVERAGE_FAULT,
    STOPPED_FAULT,
    Log,
    car_main,
)


def _describe_exit(status: int | None) -> str:
    if status is None:
        return STOPPED_FAULT
    if os.WIFSIGNALED(status):
        number = os.WTERMSIG(status)
        try:
            name = signal.Signals(number).name
        except ValueError:
            name = f"signal {number}"
        return f"{STOPPED_FAULT} (tué par le signal {name})"
    return f"{STOPPED_FAULT} (code de sortie {os.waitstatus_to_exitcode(status)})"


class Car:
    """One team's car process, seen from the worker.

    Each `send_` call goes down the pipe; `collect` waits for the replies,
    and `take` hands back each car's result, once. A call that fails, in
    any way, sets `fault` to the team's reason and kills the car's
    process; every later call fails with the same reason, without being
    sent.

    `clock` is the race time each call's log lines are stamped with: 0
    until the engine exists, which covers the import and `reset`.
    """

    def __init__(self, name: str, pid: int, calls: int, replies: int) -> None:
        self.name = name
        self.pid = pid
        self.log = Log()
        self.clock: Callable[[], float] = lambda: 0.0
        self.fault: str | None = None
        self._calls = calls
        self._replies = replies
        self._reaped = False
        self._call: str | None = None
        self._outbox = b""
        self._inbox = bytearray()
        self._sent_at = 0.0
        self._deadline = 0.0
        self._stamp = 0.0
        self._result: tuple[bool, Any] | None = None
        self._steps: deque[float] = deque(maxlen=car_process.STEP_AVERAGE_WINDOW)

    @classmethod
    def start(cls, name: str) -> "Car":
        """Fork the car's process. Call it before any team's source is in
        this process: the child starts with a copy of everything here."""
        calls_read, calls_write = os.pipe()
        replies_read, replies_write = os.pipe()
        parent = os.getpid()
        pid = os.fork()
        if pid == 0:
            car_main(calls_read, replies_write, parent)
        os.close(calls_read)
        os.close(replies_write)
        os.set_blocking(calls_write, False)
        car = cls(name, pid, calls_write, replies_read)
        # The child's first word, once it is set up, is a hello: wait for it
        # like for the reply to a call.
        car._expect("hello")
        return car

    # -- calls --

    def send_load(self, source: str, path: str, baselines: dict[str, Any]) -> None:
        """Send the import of `source`, as the file `path` names.

        `path` is made here, a symlink to the car's own copy of its source,
        since the car itself may write nothing."""
        if self.fault is None:
            Path(path).symlink_to(f"/proc/self/fd/{SOURCE_FD}")
        self._send("load", (source, path, baselines))

    def send_reset(self, track_info: TrackInfo) -> None:
        self._send("reset", track_info)

    def send_step(self, observation: Observation) -> None:
        self._send("step", tuple(observation))

    def take(self) -> tuple[bool, Any]:
        """The last call's result, once it is in: `(True, value)`, or
        `(False, fault)`."""
        result, self._result = self._result, None
        assert result is not None, "no result: collect the call first"
        return result

    @property
    def waiting(self) -> bool:
        """Whether a call is in flight."""
        return self._call is not None

    def _send(self, kind: str, args: Any) -> None:
        if self.fault is not None:
            self._result = (False, self.fault)
            return
        payload = pickle.dumps((kind, args), protocol=pickle.HIGHEST_PROTOCOL)
        self._expect(kind)
        self._outbox = FRAME.pack(len(payload)) + payload
        self._flush()

    def _expect(self, kind: str) -> None:
        """Wait, from now on, for the reply to a call of `kind`."""
        limit, _ = car_process.call_limit(kind)
        now = time.monotonic()
        self._call = kind
        self._stamp = self.clock()
        self._sent_at = now
        self._deadline = now + limit
        self._result = None

    # -- driven by `collect` --

    def _flush(self) -> None:
        try:
            written = os.write(self._calls, self._outbox)
        except BlockingIOError:
            return
        except OSError:  # the car closed its end: it is gone
            self._stopped()
            return
        self._outbox = self._outbox[written:]

    def _receive(self, now: float) -> None:
        try:
            data = os.read(self._replies, 1 << 16)
        except OSError:
            data = b""
        if not data:
            self._stopped()
            return
        self._inbox += data
        if len(self._inbox) < FRAME.size:
            return
        (size,) = FRAME.unpack_from(self._inbox)
        limit = REPLY_OVERHEAD + 9 * self.log.left
        if size > limit or len(self._inbox) > FRAME.size + size:
            self._fail(GARBLED_FAULT)
            return
        if len(self._inbox) == FRAME.size + size:
            frame = bytes(self._inbox[FRAME.size :])
            self._inbox.clear()
            self._arrived(frame, now)

    def _arrived(self, frame: bytes, now: float) -> None:
        kind = self._call
        try:
            reply = json.loads(frame)
            if not isinstance(reply, dict):
                raise ValueError
        except Exception:  # RecursionError too: anything unreadable
            self._fail(GARBLED_FAULT)
            return
        if not self.log.add(self._stamp, reply.get("o", []), reply.get("t", False)):
            self._fail(GARBLED_FAULT)
            return
        if "s" in reply:
            print(
                f"race_worker: {self.name}'s car could not be sandboxed: "
                f"{str(reply['s'])[:FAULT_CHARS]}",
                file=sys.stderr,
                flush=True,
            )
            self._fail(SANDBOX_FAULT)
        elif "m" in reply:
            self._fail(MEMORY_FAULT)
        elif "e" in reply:
            self._fail(str(reply["e"])[:FAULT_CHARS])
        elif kind == "step":
            command = _command(reply.get("c"))
            if command is None:
                self._fail(GARBLED_FAULT)
            elif self._over_average(now - self._sent_at):
                self._fail(STEP_AVERAGE_FAULT)
            else:
                self._done((True, command))
        else:
            self._done((True, None) if kind == "reset" else None)

    def _over_average(self, duration: float) -> bool:
        steps = self._steps
        steps.append(duration)
        window = steps.maxlen or 0
        return (
            len(steps) == window
            and sum(steps) > car_process.STEP_AVERAGE_LIMIT * window
        )

    def _expire(self, now: float) -> None:
        if self._call is not None and now >= self._deadline:
            self._fail(car_process.call_limit(self._call)[1])

    def _done(self, result: tuple[bool, Any] | None) -> None:
        self._call = None
        self._outbox = b""
        self._result = result

    def _fail(self, reason: str) -> None:
        self.fault = reason
        self.kill()
        self._done((False, reason))

    def _stopped(self) -> None:
        """The pipe closed: say why, from the exit status if there is one."""
        status = None
        deadline = time.monotonic() + EXIT_GRACE
        while not self._reaped:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self._reaped = True
            elif time.monotonic() > deadline:
                status = None
                break
            else:
                time.sleep(0.005)
        if self._call == "hello":
            # Before any team code: its own setup failed, the sandbox's
            # syscalls refused by the unit, say. The admin needs the cause.
            print(
                f"race_worker: {self.name}'s car could not be sandboxed: its "
                f"process ended during setup ({_describe_exit(status)})",
                file=sys.stderr,
                flush=True,
            )
            self._fail(SANDBOX_FAULT)
            return
        self._fail(_describe_exit(status))

    # -- the end --

    def kill(self) -> None:
        """SIGKILL the car's process. Safe to repeat: until it is reaped, its
        pid cannot be anyone else's."""
        if not self._reaped:
            try:
                os.kill(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def close(self) -> None:
        """Kill the process, reap it, and close the pipe."""
        self.kill()
        if not self._reaped:
            try:
                os.waitpid(self.pid, 0)
            except ChildProcessError:
                pass  # someone else reaped it
            self._reaped = True
        for fd in (self._calls, self._replies):
            try:
                os.close(fd)
            except OSError:
                pass
        self._calls = self._replies = -1


def _command(value: object) -> tuple[float, float] | None:
    """A reply's command, if it is two finite numbers, as a car sends it.

    The engine judges every command again (`validate_command`), but a car's
    own wrapper never sends anything else: a reply that is not two finite
    numbers was forged past it, and is garbled, not a bad command.
    """
    if not isinstance(value, list) or len(value) != 2:
        return None
    speed, steering = value
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
        return None
    pair = (float(speed), float(steering))
    if not all(math.isfinite(v) for v in pair):
        return None
    return pair


def collect(cars: Sequence[Car]) -> None:
    """Wait for every car's call in flight to be answered, or to fail.

    Each call has its own deadline, counted from its send, so the cars run
    in parallel and the wait lasts as long as the slowest. A car whose call
    is still being written (a big `reset`, a pipe the car shrank) gets the
    rest of it as its pipe drains.
    """
    pending = {car._replies: car for car in cars if car.waiting}
    poller = select.poll()
    writing: dict[int, Car] = {}
    for fd in pending:
        poller.register(fd, select.POLLIN)
    while pending:
        for car in pending.values():
            if car._outbox and car._calls not in writing:
                poller.register(car._calls, select.POLLOUT)
                writing[car._calls] = car
        now = time.monotonic()
        wait = min(car._deadline for car in pending.values()) - now
        events = poller.poll(max(0.0, wait) * 1000) if wait > 0 else []
        now = time.monotonic()
        for fd, _ in events:
            if fd in writing:
                car = writing[fd]
                if car.waiting:
                    car._flush()
                if not car._outbox:
                    poller.unregister(fd)
                    del writing[fd]
            elif fd in pending:
                pending[fd]._receive(now)
        for fd, car in list(pending.items()):
            car._expire(now)
            if not car.waiting:
                poller.unregister(fd)
                del pending[fd]
                if car._calls in writing:
                    poller.unregister(car._calls)
                    del writing[car._calls]


def start_cars(names: Sequence[str]) -> list[Car]:
    """Fork one car per name, and wait until each is set up and sandboxed,
    or has failed to be.

    The objects this process holds now are frozen out of the garbage
    collector first, so the cars do not each copy every page it would
    otherwise walk.
    """
    gc.collect()
    gc.freeze()
    cars = [Car.start(name) for name in names]
    collect(cars)  # every hello: no car is still being set up
    return cars


class CarField:
    """A race's cars, as the field cocoracer's engine steps.

    Each call is sent to every car first and then every reply collected,
    so the cars run in parallel. A failed call comes back as a `Fault`
    holding a `ControllerFault` with the team's reason, which the engine
    records as it is.

    A car that would not load is not reset: it faults at its first step,
    with the reason it would not load, so until the start it waits on the
    grid like the rest of the field.
    """

    def __init__(self, cars: Sequence[Car]) -> None:
        self._cars = list(cars)

    def __len__(self) -> int:
        return len(self._cars)

    def reset(self, track_info: TrackInfo) -> list[Fault | None]:
        loaded = [car for car in self._cars if car.fault is None]
        for car in loaded:
            car.send_reset(track_info)
        collect(loaded)
        faults: list[Fault | None] = []
        for car in self._cars:
            outcome = _outcome(car) if car in loaded else None
            faults.append(outcome if isinstance(outcome, Fault) else None)
        return faults

    def step(
        self, slots: Sequence[int], observations: Sequence[Observation]
    ) -> list[object]:
        stepped = [self._cars[i] for i in slots]
        for car, observation in zip(stepped, observations, strict=True):
            car.send_step(observation)
        collect(stepped)
        return [_outcome(car) for car in stepped]


def _outcome(car: Car) -> object:
    """The car's last call's result as the field hands it to the engine:
    what the call returned, or a `Fault` with the team's reason."""
    ok, value = car.take()
    return value if ok else Fault(ControllerFault(value))
