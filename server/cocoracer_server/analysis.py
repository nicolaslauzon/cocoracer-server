"""Static analysis of a pushed controller, before it is accepted.

A push is read, never run: every check here works on the `ast` of the
source, so a team can't make the server import or execute anything by
pushing. The checks mirror what `cocoracer.controller.load_controller` will
demand when the race loads the file, plus which modules the server has, so a
team learns at push time, on its own page, what would otherwise only show up
as a fault in the next race.

No module is refused for what it could do: a car that opens a socket,
starts a process or a thread, or opens a file is stopped by its sandbox
when it tries (ADR 0007), with the error in its own log, and the rest of
the field races on. The `open` warning says so ahead of time.

Findings come in two severities. An error rejects the push, and the team's
previous submission keeps racing. A warning is shown to the team, and the
push goes through.

What static analysis can't catch, and the race still handles: a controller
that never returns (a `while True:` in `step`), one that raises at runtime,
and one that returns bad values. It also can't see through dynamic tricks
(`getattr`, `globals()`, a module reached through another module); the
warnings flag the obvious doors, not every one.

The messages are in French, because they go straight onto the team's page.
"""

import ast
import sys
from dataclasses import dataclass

# The paths a base class may name Controller by. The start kit imports it
# from the top-level package; the submodule path is what the kit used before,
# and stays accepted so a controller written against it still loads.
CONTROLLER = "cocoracer.Controller"
CONTROLLER_PATHS = frozenset({CONTROLLER, "cocoracer.controller.Controller"})

# Top-level modules a controller may import on top of the standard library.
ALLOWED_THIRD_PARTY = frozenset({"numpy", "scipy", "cocoracer"})

# What a controller may take from `cocoracer`: the names the start kit's
# `cocoracer.py` defines. A team that adds a helper to its copy of that file
# can import it on its laptop, but the server has the real package, where the
# import fails; so anything else from `cocoracer` is refused at push time.
COCORACER_API = ("Controller", "TrackInfo", "CenterlinePoint")

# The only cocoracer submodule a controller may name: the legacy path, which
# a controller written against the installed package still uses.
COCORACER_MODULES = frozenset({"cocoracer", "cocoracer.controller"})

COCORACER_API_MESSAGE = (
    "cocoracer ne fournit que Controller, TrackInfo et CenterlinePoint "
    "(ceux de cocoracer.py) ; sur le serveur, cocoracer.py est remplacé par "
    "le vrai moteur."
)

# Standard modules that are allowed but draw a warning, with the reason.
WARNED_MODULES = {
    "importlib": "importlib charge du code dynamiquement, ce que les règles interdisent.",
}

# Built-in names whose use draws a warning, with the reason.
WARNED_CALLS = {
    "open": (
        "open() : sur le serveur, ton contrôleur ne peut lire ni écrire aucun "
        "fichier (PermissionError) ; centerline.csv n'y est pas, prends le TrackInfo."
    ),
    "eval": "eval() : les règles interdisent l'exécution de code dynamique.",
    "exec": "exec() : les règles interdisent l'exécution de code dynamique.",
    "__import__": "__import__() : les règles interdisent les imports dynamiques.",
}

STEP_PARAMS = ("self", "x", "y", "yaw", "speed", "steering_angle", "laser_scan")


@dataclass(frozen=True)
class Finding:
    """One problem in a pushed source, as the team's page shows it.

    `line` is 1-based, or None when the problem belongs to the file as a
    whole (no controller class at all, say).
    """

    line: int | None
    message: str


@dataclass(frozen=True)
class Analysis:
    """Everything `check_source` found. The push is rejected iff `errors`."""

    errors: tuple[Finding, ...] = ()
    warnings: tuple[Finding, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


def check_source(source: str) -> Analysis:
    """Analyse a controller's source without importing or running it."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return Analysis(
            errors=(Finding(exc.lineno, f"Erreur de syntaxe : {exc.msg}."),)
        )
    errors: list[Finding] = []
    warnings: list[Finding] = []
    _check_imports(tree, errors, warnings)
    _check_calls(tree, warnings)
    _check_cocoracer_attributes(tree, errors)
    _check_controller_class(tree, errors)
    return Analysis(errors=_in_line_order(errors), warnings=_in_line_order(warnings))


def _in_line_order(findings: list[Finding]) -> tuple[Finding, ...]:
    """Findings top to bottom, the file-wide ones (no line) first."""
    return tuple(sorted(findings, key=lambda f: f.line or 0))


# --- imports -------------------------------------------------------------


def _check_imports(
    tree: ast.Module, errors: list[Finding], warnings: list[Finding]
) -> None:
    """Every import anywhere in the file, including inside functions."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check_module(alias.name, node.lineno, errors, warnings)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                errors.append(
                    Finding(
                        node.lineno,
                        "Import relatif interdit : le contrôleur doit tenir en un seul fichier.",
                    )
                )
                continue
            _check_module(node.module or "", node.lineno, errors, warnings)
            if node.module == "cocoracer":
                for alias in node.names:
                    if alias.name != "*" and alias.name not in COCORACER_API:
                        errors.append(
                            Finding(
                                node.lineno,
                                f"from cocoracer import {alias.name} : "
                                + COCORACER_API_MESSAGE,
                            )
                        )
            if any(alias.name == "*" for alias in node.names):
                warnings.append(
                    Finding(
                        node.lineno,
                        f"from {node.module} import * : importe plutôt les noms "
                        f"dont tu as besoin.",
                    )
                )


def _check_module(
    name: str, line: int, errors: list[Finding], warnings: list[Finding]
) -> None:
    top = name.split(".")[0]
    if top == "cocoracer" and name not in COCORACER_MODULES:
        errors.append(
            Finding(
                line,
                f"Le module {name} n'est pas disponible : " + COCORACER_API_MESSAGE,
            )
        )
    elif top not in sys.stdlib_module_names and top not in ALLOWED_THIRD_PARTY:
        errors.append(
            Finding(
                line,
                f"Le module {top} n'est pas disponible : seuls la bibliothèque standard, "
                f"numpy, scipy et cocoracer le sont.",
            )
        )
    elif top in WARNED_MODULES:
        warnings.append(Finding(line, WARNED_MODULES[top]))


def _check_cocoracer_attributes(tree: ast.Module, errors: list[Finding]) -> None:
    """`cocoracer.X` after `import cocoracer` must name the API too."""
    modules = {
        name for name, path in _import_names(tree).items() if path == "cocoracer"
    }
    allowed = {*COCORACER_API, "controller"}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in modules
            and node.attr not in allowed
        ):
            errors.append(
                Finding(
                    node.lineno, f"cocoracer.{node.attr} : " + COCORACER_API_MESSAGE
                )
            )


# --- calls ---------------------------------------------------------------


def _check_calls(tree: ast.Module, warnings: list[Finding]) -> None:
    """Uses of the warned built-ins, called or merely referenced."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in WARNED_CALLS:
            warnings.append(Finding(node.lineno, WARNED_CALLS[node.id]))


# --- the controller class ------------------------------------------------


def _check_controller_class(tree: ast.Module, errors: list[Finding]) -> None:
    """Exactly one top-level Controller subclass, loadable as the race loads it."""
    names = _import_names(tree)
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    controllers = _controller_subclasses(tree, names)
    if not controllers:
        errors.append(
            Finding(
                None,
                "Aucune classe ne dérive de Controller : définis-en une, par exemple "
                "class MyCar(Controller), après from cocoracer import Controller.",
            )
        )
        return
    if len(controllers) > 1:
        found = ", ".join(c.name for c in controllers)
        errors.append(
            Finding(
                controllers[1].lineno,
                f"Le fichier doit définir exactement une classe dérivée de Controller "
                f"(trouvées : {found}).",
            )
        )
        return
    cls = controllers[0]
    _check_step(cls, classes, errors)
    _check_init(cls, classes, errors)


def _import_names(tree: ast.Module) -> dict[str, str]:
    """What each module-level imported name refers to, as a dotted path.

    `import cocoracer.controller` binds `cocoracer`; `from cocoracer import
    controller as c` binds `c` to `cocoracer.controller`. A star import from
    `cocoracer` or `cocoracer.controller` binds `Controller` itself.
    """
    names: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    names[top] = top
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            for alias in node.names:
                if alias.name == "*":
                    if node.module in ("cocoracer", "cocoracer.controller"):
                        names["Controller"] = f"{node.module}.Controller"
                else:
                    names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _dotted(expr: ast.expr, names: dict[str, str]) -> str | None:
    """The dotted path a base-class expression names, through the imports."""
    if isinstance(expr, ast.Name):
        return names.get(expr.id)
    if isinstance(expr, ast.Attribute):
        owner = _dotted(expr.value, names)
        return f"{owner}.{expr.attr}" if owner else None
    return None


def _controller_subclasses(
    tree: ast.Module, names: dict[str, str]
) -> list[ast.ClassDef]:
    """Top-level classes that derive from Controller, directly or not.

    A class deriving from another class of the same file that derives from
    Controller counts too, as it does for the loader.
    """
    found: list[ast.ClassDef] = []
    local: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if _dotted(base, names) in CONTROLLER_PATHS or (
                isinstance(base, ast.Name) and base.id in local
            ):
                found.append(node)
                local.add(node.name)
                break
    return found


def _find_method(
    cls: ast.ClassDef, classes: dict[str, ast.ClassDef], name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """A method defined on the class or inherited from a class of this file.

    Controller's own methods don't count: inheriting its `step` is exactly
    what the loader rejects, and its `__init__` takes no arguments.
    """
    seen: set[str] = set()
    todo = [cls]
    while todo:
        current = todo.pop(0)
        if current.name in seen:
            continue
        seen.add(current.name)
        for stmt in current.body:
            if (
                isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
                and stmt.name == name
            ):
                return stmt
        todo.extend(
            classes[b.id]
            for b in current.bases
            if isinstance(b, ast.Name) and b.id in classes
        )
    return None


def _check_step(
    cls: ast.ClassDef, classes: dict[str, ast.ClassDef], errors: list[Finding]
) -> None:
    step = _find_method(cls, classes, "step")
    signature = "step(self, " + ", ".join(STEP_PARAMS[1:]) + ")"
    if step is None:
        errors.append(
            Finding(
                cls.lineno, f"La classe {cls.name} doit définir la méthode {signature}."
            )
        )
        return
    positional = len(step.args.posonlyargs) + len(step.args.args)
    if positional < len(STEP_PARAMS):
        errors.append(
            Finding(
                step.lineno,
                f"La méthode step de {cls.name} doit prendre {len(STEP_PARAMS)} paramètres "
                f"positionnels : {signature}.",
            )
        )


def _check_init(
    cls: ast.ClassDef, classes: dict[str, ast.ClassDef], errors: list[Finding]
) -> None:
    """The class must be instantiable with no arguments, or with `baselines`."""
    init = _find_method(cls, classes, "__init__")
    if init is None:
        return
    args = init.args
    positional = args.posonlyargs + args.args
    defaults_from = len(positional) - len(args.defaults)
    required = [a.arg for i, a in enumerate(positional) if i >= 1 and i < defaults_from]
    required += [
        a.arg
        for a, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
        if default is None
    ]
    required = [name for name in required if name != "baselines"]
    if required:
        errors.append(
            Finding(
                init.lineno,
                f"Le constructeur de {cls.name} doit pouvoir être appelé sans arguments "
                f"(paramètres obligatoires : {', '.join(required)}).",
            )
        )
