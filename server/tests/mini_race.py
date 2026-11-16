"""A race on a short oval, every car in its own process, run from a test.

The oval is a copy of `cocoracer/tests/mini_track.py`'s `MINI_SPEC`, which
the server's tests cannot import; per `AGENTS.md`, nothing here races on a
shipped track. No car needs to go round it: the field gets past the
countdown and takes a few steps, which is all the tests need.

The cars are forked from the test process itself, with the worker's own
`start_cars` and `field`, so a test drives the real car processes and their
pipes without a race worker in between. `step_car` and `reset_car` make
one call to one car through the worker's own `CarField`.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from cocoracer.config import DEFAULT_PARAMS, Segment, TrackSpec, load_config
from cocoracer.controller import make_track_info
from cocoracer.engine import RaceEngine
from cocoracer.field import Fault, Observation
from cocoracer.track import Track, build_track

from cocoracer_server import car_field, race_worker
from cocoracer_server.car_field import Car, CarField

MINI_SPEC = TrackSpec(
    name="mini",
    width=20.0,
    resolution=1.0,
    segments=[
        Segment("straight", length=10.0),
        Segment("turn", radius=12.0, angle=180.0),
        Segment("straight", length=10.0),
        Segment("turn", radius=12.0, angle=180.0),
    ],
)

# The countdown in ticks, as the engine counts it from the params: no car
# steps before it is over.
_CONFIG = load_config(DEFAULT_PARAMS)
TICK_DT = _CONFIG.sim.tick_dt
COUNTDOWN_TICKS = int(round(_CONFIG.race.countdown / TICK_DT))

SITS = """
from cocoracer.controller import Controller

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        return 0.0, 0.0
"""

DRIVES = """
from cocoracer.controller import Controller

class Car(Controller):
    def step(self, x, y, yaw, speed, steering_angle, laser_scan):
        return 1.0, 0.0
"""

_TRACK: list[Track] = []


def mini_track() -> Track:
    """The oval, built once per test session."""
    if not _TRACK:
        _TRACK.append(build_track(MINI_SPEC))
    return _TRACK[0]


@contextmanager
def started(sources: Sequence[str], directory: Path) -> Iterator[list[Car]]:
    """`sources`' cars, forked and loaded but not reset, killed at the end."""
    config = load_config(DEFAULT_PARAMS)
    cars = car_field.start_cars([f"car{i}" for i in range(len(sources))])
    try:
        for i, (car, source) in enumerate(zip(cars, sources, strict=True)):
            car.send_load(source, str(directory / f"car{i}.py"), config.baselines)
        car_field.collect(cars)
        yield cars
    finally:
        for car in cars:
            car.close()


@contextmanager
def on_the_grid(
    sources: Sequence[str], directory: Path
) -> Iterator[tuple[RaceEngine, list[Car]]]:
    """A race of `sources` on the oval, built as the worker builds it and
    waiting for its start; its cars are killed at the end."""
    config = load_config(DEFAULT_PARAMS)
    names = [f"car{i}" for i in range(len(sources))]
    cars = car_field.start_cars(names)
    try:
        field = race_worker.field(cars, sources, directory, config.baselines)
        engine = RaceEngine(
            mini_track(),
            config,
            names=names,
            mode="race",
            auto_start=False,
            field=field,
        )
        for car in cars:
            car.clock = lambda: engine.time
        yield engine, cars
    finally:
        for car in cars:
            car.close()


@contextmanager
def racing(
    sources: Sequence[str], directory: Path, steps: int = 10
) -> Iterator[tuple[RaceEngine, list[Car]]]:
    """A race of `sources` on the oval, `steps` ticks past the countdown,
    as the worker runs it; its cars are killed at the end."""
    with on_the_grid(sources, directory) as (engine, cars):
        engine.start()
        for _ in range(COUNTDOWN_TICKS + steps):
            engine.tick()
        yield engine, cars


def statuses(engine: RaceEngine) -> list[str]:
    return [v.state.status.value for v in engine.vehicles]


def faults(engine: RaceEngine) -> list[str | None]:
    return [v.state.record().fault_reason for v in engine.vehicles]


def _settled(outcome: object) -> object:
    if isinstance(outcome, Fault):
        raise outcome.exception
    return outcome


def step_car(car: Car, scan: np.ndarray) -> object:
    """Step `car` alone, from the origin at rest: its command, or its
    fault raised as the `ControllerFault` the engine would record."""
    observation = Observation(0.0, 0.0, 0.0, 0.0, 0.0, scan)
    return _settled(CarField([car]).step([0], [observation])[0])


def reset_car(car: Car) -> None:
    """Reset `car` alone on the oval; its fault raised, as `step_car`'s."""
    _settled(CarField([car]).reset(make_track_info(mini_track()))[0])
