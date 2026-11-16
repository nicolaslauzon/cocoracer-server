"""The start kit against the real cocoracer: its API file and its CSV.

Teams never install cocoracer. The kit's `cocoracer.py` stands in for it on
their laptops, and the same import line in `my_car.py` reaches the real
package on the server, so the two must describe the same API. The server's
tests can import both, which is why this lives here.
"""

import dataclasses
import importlib.util
import inspect
import re
import sys
import types
import typing
from pathlib import Path

import cocoracer
import pytest
from cocoracer.config import DEFAULT_PARAMS, load_config

REPO = Path(__file__).resolve().parents[2]
KIT = REPO / "start_kit"


def _load(path: Path, name: str) -> types.ModuleType:
    """Import a file under a name of our own, so it can't shadow a package."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def kit() -> types.ModuleType:
    return _load(KIT / "cocoracer.py", "start_kit_cocoracer")


def _type_name(hint: object) -> str:
    """A type as text, classes by bare name: the kit's and the package's
    `CenterlinePoint` are different objects that must read the same."""
    origin = typing.get_origin(hint)
    if origin is not None:
        args = ", ".join(
            "..." if a is Ellipsis else _type_name(a) for a in typing.get_args(hint)
        )
        return f"{_type_name(origin)}[{args}]"
    if isinstance(hint, type):
        return hint.__qualname__
    return repr(hint)


def _fields(cls: type) -> list[tuple[str, str, object]]:
    hints = typing.get_type_hints(cls)
    return [
        (f.name, _type_name(hints[f.name]), f.default) for f in dataclasses.fields(cls)
    ]


def _signature(func: object) -> list[tuple[str, str, object]]:
    sig = inspect.signature(func)  # type: ignore[arg-type]
    hints = typing.get_type_hints(func)
    params = [
        (name, _type_name(hints.get(name, "")), p.default)
        for name, p in sig.parameters.items()
    ]
    return params + [("return", _type_name(hints.get("return")), None)]


class TestApiFileMatchesCocoracer:
    def test_the_kit_exports_the_controller_api(self, kit: types.ModuleType) -> None:
        assert sorted(kit.__all__) == ["CenterlinePoint", "Controller", "TrackInfo"]

    def test_every_kit_name_is_exported_by_cocoracer(
        self, kit: types.ModuleType
    ) -> None:
        for name in kit.__all__:
            assert name in cocoracer.__all__
            assert hasattr(cocoracer, name)

    @pytest.mark.parametrize("name", ["CenterlinePoint", "TrackInfo"])
    def test_dataclass_fields_match(self, kit: types.ModuleType, name: str) -> None:
        ours, theirs = getattr(kit, name), getattr(cocoracer, name)
        assert _fields(ours) == _fields(theirs)
        assert ours.__dataclass_params__.frozen == theirs.__dataclass_params__.frozen

    def test_controller_methods_match(self, kit: types.ModuleType) -> None:
        def public(cls: type) -> set[str]:
            return {n for n in vars(cls) if not n.startswith("_")}

        assert public(kit.Controller) == public(cocoracer.Controller)
        for method in ("reset", "step"):
            ours = getattr(kit.Controller, method)
            theirs = getattr(cocoracer.Controller, method)
            assert _signature(ours) == _signature(theirs)

    def test_a_field_missing_from_the_kit_is_caught(self) -> None:
        """The comparison sees a field added on one side only."""

        @dataclasses.dataclass(frozen=True)
        class TrackInfo(cocoracer.TrackInfo):
            surface: str = "asphalt"

        assert _fields(TrackInfo) != _fields(cocoracer.TrackInfo)


class TestStepDocstringStatesTheLimits:
    """The signatures can't carry the limits, so the kit's prose does.

    The comparisons above see names, types and defaults, not docstrings, so
    a change to the car's limits in `params/default.yaml` left the kit
    saying the old ones. These read the numbers back out of the prose.
    """

    @staticmethod
    def _step_doc(kit: types.ModuleType) -> str:
        return inspect.getdoc(kit.Controller.step) or ""

    def test_target_speed_clamp_is_the_server_speed_range(
        self, kit: types.ModuleType
    ) -> None:
        vehicle = load_config(DEFAULT_PARAMS).vehicle
        match = re.search(
            r"`target_speed`.*?clamped to \[(\S+), (\S+)\]",
            self._step_doc(kit),
            re.DOTALL,
        )
        assert match is not None
        assert (float(match[1]), float(match[2])) == (
            vehicle.min_speed,
            vehicle.max_speed,
        )

    def test_steering_clamp_is_the_server_steering_range(
        self, kit: types.ModuleType
    ) -> None:
        vehicle = load_config(DEFAULT_PARAMS).vehicle
        assert vehicle.min_steer == -vehicle.max_steer
        match = re.search(
            r"`target_steering_angle`.*?clamped to \+/- (\S+?),",
            self._step_doc(kit),
            re.DOTALL,
        )
        assert match is not None
        assert float(match[1]) == vehicle.max_steer


class TestMyCar:
    def test_my_car_imports_against_the_kit_alone(
        self, kit: types.ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """As on a laptop: `cocoracer` is the kit's file, not the package."""
        monkeypatch.setitem(sys.modules, "cocoracer", kit)
        my_car = _load(KIT / "my_car.py", "start_kit_my_car")
        assert issubclass(my_car.MyCar, kit.Controller)

    def test_my_car_loads_against_the_real_package(self) -> None:
        """As on the server: the same file, the real `Controller`."""
        car = cocoracer.load_controller(KIT / "my_car.py")
        assert isinstance(car, cocoracer.Controller)


def test_the_kit_centerline_csv_is_current() -> None:
    """Regenerated in memory: a track change cannot leave the kit's stale.

    If this fails, run `server/.venv/bin/python scripts/write_centerline_csv.py`.
    """
    script = _load(REPO / "scripts" / "write_centerline_csv.py", "write_centerline")
    assert (KIT / "centerline.csv").read_text() == script.event_centerline_csv()
