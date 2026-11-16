"""Static analysis of pushed controllers: every finding, and the real files."""

from pathlib import Path
from textwrap import dedent

import pytest

from cocoracer_server.analysis import Analysis, Finding, check_source

REPO = Path(__file__).resolve().parents[2]

HEADER = "from cocoracer import Controller\n"
STEP = (
    "    def step(self, x, y, yaw, speed, steering_angle, laser_scan):\n"
    "        return 1.0, 0.0\n"
)
CAR = HEADER + "\nclass Car(Controller):\n" + STEP


def _check(source: str) -> Analysis:
    return check_source(dedent(source))


def _messages(findings: tuple[Finding, ...]) -> str:
    return "\n".join(f.message for f in findings)


class TestRealControllers:
    """The start kit and the shipped baselines are what a team could write."""

    @pytest.mark.parametrize(
        "path",
        [REPO / "start_kit" / "my_car.py"]
        + sorted(
            path
            for path in (REPO / "cocoracer" / "controllers").glob("*.py")
            # A calibration check, not a baseline: it subclasses the
            # raceline pursuit, read from disk, which a car on the server
            # can't do.
            if path.name != "braking_pursuit.py"
        ),
        ids=lambda p: p.name,
    )
    def test_passes_clean(self, path: Path) -> None:
        analysis = check_source(path.read_text())
        assert analysis.errors == ()
        assert analysis.warnings == ()
        assert analysis.ok

    def test_minimal_car_passes_clean(self) -> None:
        assert check_source(CAR) == Analysis()


class TestSyntax:
    def test_syntax_error_is_reported_with_its_line(self) -> None:
        analysis = check_source(CAR + "\ndef broken(:\n")
        assert not analysis.ok
        [error] = analysis.errors
        assert error.line == 7
        assert "syntaxe" in error.message


class TestImports:
    @pytest.mark.parametrize("line", ["import pandas", "from torch.nn import Linear"])
    def test_unavailable_module_is_an_error(self, line: str) -> None:
        analysis = check_source(line + "\n" + CAR)
        [error] = analysis.errors
        assert error.line == 1
        assert "n'est pas disponible" in error.message

    @pytest.mark.parametrize(
        "line",
        [
            "import math",
            "import numpy as np",
            "from scipy.signal import butter",
            "from cocoracer import TrackInfo, CenterlinePoint",
            "from cocoracer.controller import TrackInfo",
            "from collections import deque",
        ],
    )
    def test_allowed_module_is_fine(self, line: str) -> None:
        assert check_source(line + "\n" + CAR) == Analysis()

    @pytest.mark.parametrize(
        "line",
        [
            # A helper a team added to its own copy of the kit's cocoracer.py:
            # it imports on the laptop, and fails on the server.
            "from cocoracer import Controller as C, angle_diff",
            "from cocoracer import controller",
            "import cocoracer.track",
            "from cocoracer.engine import RaceEngine",
            "import cocoracer\nX = cocoracer.angle_diff",
        ],
    )
    def test_anything_from_cocoracer_beyond_the_kit_is_an_error(
        self, line: str
    ) -> None:
        analysis = check_source(line + "\n" + CAR)
        [error] = analysis.errors
        assert error.line == line.count("\n") + 1
        assert "Controller, TrackInfo et CenterlinePoint" in error.message

    def test_cocoracer_attribute_of_the_api_is_fine(self) -> None:
        source = "import cocoracer\n\nclass Car(cocoracer.Controller):\n" + STEP
        source += "        cocoracer.TrackInfo\n"
        assert check_source(source).errors == ()

    def test_relative_import_is_an_error(self) -> None:
        analysis = check_source("from . import helpers\n" + CAR)
        [error] = analysis.errors
        assert error.line == 1
        assert "relatif" in error.message

    @pytest.mark.parametrize(
        "module",
        [
            "socket",
            "ssl",
            "http.client",
            "urllib.request",
            "subprocess",
            "multiprocessing",
            "ctypes",
        ],
    )
    def test_network_and_process_modules_are_accepted(self, module: str) -> None:
        """The car's sandbox, not the analysis, stops a car that uses them
        (ADR 0007): the import goes through, the call fails at run time."""
        analysis = check_source(f"import {module}\n" + CAR)
        assert analysis.ok
        assert analysis.warnings == ()

    def test_import_inside_a_function_is_checked(self) -> None:
        analysis = check_source(
            CAR + "\n    def reset(self, info):\n        import pandas\n"
        )
        [error] = analysis.errors
        assert error.line == 8

    def test_star_import_is_a_warning(self) -> None:
        analysis = check_source("from math import *\n" + CAR)
        assert analysis.ok
        [warning] = analysis.warnings
        assert "import *" in warning.message

    def test_importlib_is_a_warning(self) -> None:
        analysis = check_source("import importlib\n" + CAR)
        assert analysis.ok
        [warning] = analysis.warnings
        assert "importlib" in warning.message


class TestCalls:
    @pytest.mark.parametrize("name", ["open", "eval", "exec", "__import__"])
    def test_warned_builtin_is_a_warning_with_its_line(self, name: str) -> None:
        source = CAR + f"\n    def reset(self, info):\n        {name}('x')\n"
        analysis = check_source(source)
        assert analysis.ok
        [warning] = analysis.warnings
        assert warning.line == 8
        assert name in warning.message

    def test_print_is_not_a_warning(self) -> None:
        """What a car prints is its log, read on the race's replay page."""
        source = CAR + "\n    def reset(self, info):\n        print('x')\n"
        assert check_source(source).warnings == ()


class TestControllerClass:
    def test_no_controller_class_is_an_error(self) -> None:
        analysis = _check(
            """
            class Car:
                def step(self, x, y, yaw, speed, steering_angle, laser_scan):
                    return 1.0, 0.0
            """
        )
        [error] = analysis.errors
        assert error.line is None
        assert "Aucune classe" in error.message
        assert "from cocoracer import Controller" in error.message

    def test_two_controller_classes_is_an_error(self) -> None:
        analysis = check_source(CAR + "\nclass Other(Controller):\n" + STEP)
        [error] = analysis.errors
        assert "Car, Other" in error.message

    def test_indirect_subclass_counts_like_the_loader(self) -> None:
        source = (
            HEADER + "class Base(Controller):\n    pass\n\nclass Car(Base):\n" + STEP
        )
        [error] = check_source(source).errors
        assert "Base, Car" in error.message

    def test_nested_class_does_not_count(self) -> None:
        source = CAR + "\ndef make():\n    class Inner(Controller):\n        pass\n"
        assert check_source(source) == Analysis()

    @pytest.mark.parametrize(
        "header, base",
        [
            ("from cocoracer import Controller as Base", "Base"),
            ("import cocoracer", "cocoracer.Controller"),
            ("import cocoracer as cr", "cr.Controller"),
            # The submodule path the kit used before it shipped its own API
            # file: still accepted, so an older controller still loads.
            ("from cocoracer.controller import Controller", "Controller"),
            ("from cocoracer.controller import Controller as Base", "Base"),
            ("import cocoracer.controller", "cocoracer.controller.Controller"),
            ("import cocoracer.controller as cc", "cc.Controller"),
        ],
    )
    def test_controller_may_be_reached_in_several_ways(
        self, header: str, base: str
    ) -> None:
        source = f"{header}\n\nclass Car({base}):\n" + STEP
        assert check_source(source) == Analysis()

    @pytest.mark.parametrize("module", ["cocoracer", "cocoracer.controller"])
    def test_star_import_of_controller_resolves(self, module: str) -> None:
        source = f"from {module} import *\n\nclass Car(Controller):\n" + STEP
        analysis = check_source(source)
        assert analysis.ok
        assert len(analysis.warnings) == 1

    def test_a_different_controller_does_not_count(self) -> None:
        source = "from mylib import Controller\n\nclass Car(Controller):\n" + STEP
        messages = _messages(check_source(source).errors)
        assert "Aucune classe" in messages

    def test_missing_step_is_an_error(self) -> None:
        analysis = check_source(HEADER + "\nclass Car(Controller):\n    pass\n")
        [error] = analysis.errors
        assert error.line == 3
        assert "step" in error.message

    def test_short_step_is_an_error(self) -> None:
        analysis = check_source(
            HEADER
            + "\nclass Car(Controller):\n    def step(self, x, y, yaw):\n        pass\n"
        )
        [error] = analysis.errors
        assert error.line == 4
        assert "7 paramètres" in error.message

    def test_step_inherited_from_a_local_base_is_found(self) -> None:
        source = (
            HEADER
            + "class Mixin:\n"
            + STEP
            + "\nclass Car(Mixin, Controller):\n    pass\n"
        )
        assert check_source(source) == Analysis()

    @pytest.mark.parametrize(
        "signature",
        [
            "self",
            "self, baselines",
            "self, baselines=None",
            "self, gain=1.0, *, trim=0.0",
            "self, *args, **kwargs",
        ],
    )
    def test_init_callable_without_arguments_is_fine(self, signature: str) -> None:
        source = CAR + f"\n    def __init__({signature}):\n        pass\n"
        assert check_source(source) == Analysis()

    @pytest.mark.parametrize(
        "signature, missing",
        [("self, gain", "gain"), ("self, baselines, *, trim", "trim")],
    )
    def test_init_with_required_arguments_is_an_error(
        self, signature: str, missing: str
    ) -> None:
        source = CAR + f"\n    def __init__({signature}):\n        pass\n"
        [error] = check_source(source).errors
        assert error.line == 7
        assert missing in error.message
        assert "baselines" not in error.message
