"""The sandbox every car's process puts itself in before it loads team code.

A car's process runs as the same user as the race worker, its sibling cars
and the server, so without a sandbox its controller could kill any of them,
start processes, open sockets, or read and write the event's files. Two
layers (ADR 0007), both installed before the team's file is imported:

**A seccomp filter** (`install`). Default allow; a refused call fails with
`EPERM`, which Python raises as an ordinary `PermissionError` in the
controller that made it.

- `kill`, `tgkill`, `tkill`, `rt_sigqueueinfo` and `rt_tgsigqueueinfo`
  reach only the car's own pid, so not its process group (`kill(0)`,
  `killpg`) nor every process (`kill(-1)`); `pidfd_send_signal` nothing.
- No `clone` or `clone3` at all: one thread per car, and no new process.
  A thread fails to start (Python's `RuntimeError: can't start new
  thread`); `fork` and `vfork` are refused too.
- No `execve`, `ptrace`, `process_vm_readv`/`writev`, `process_madvise`,
  `pidfd_getfd`, `socket`, nor `io_uring_setup` (io_uring can open a
  socket without the `socket` call).
- The calls that act on another process by pid, `setpriority`,
  `ioprio_set`, `sched_setaffinity`, `sched_setscheduler`,
  `sched_setparam`, `sched_setattr`, `prlimit64`, `migrate_pages` and
  `move_pages`, only on the caller itself: pid 0, and for `setpriority`
  and `ioprio_set` only the "process" kind, since pid 0 with the
  process-group or user kind means the car's group or every process of
  its user. So a car cannot lower the priority of the worker or a
  sibling. (Its own pid, rather than 0, is refused too: libseccomp takes
  one comparison per argument and rule.)
- `prctl(PR_SET_PDEATHSIG)` is refused, so a car cannot undo the SIGKILL it
  is promised when the worker dies.

**A Landlock ruleset** (`restrict_files`). No file may be written, created
or removed anywhere, and the only files that may be read are the libraries
a later import reads: the standard library, numpy, scipy and cocoracer
(`library_paths`). The event's state, the race's directory, the other
cars' sources, `/etc`: a `PermissionError`. Where the kernel supports
them, TCP and signals outside the car are refused by Landlock as well.

The seccomp bindings are PyPI's `pyseccomp`, ctypes over the system's
libseccomp; Landlock is three syscalls, made through ctypes.
`python -m cocoracer_server.sandbox` installs both in a throwaway process and
checks they hold; `deploy.sh` runs it and refuses a machine where they do
not.
"""

import ctypes
import errno
import os
import socket
import sys
import sysconfig
import threading
from pathlib import Path
from typing import Any

# prctl's option for the signal sent on the parent's death (linux/prctl.h).
PR_SET_PDEATHSIG = 1

# Refused outright.
REFUSED = (
    "fork",
    "vfork",
    "execve",
    "execveat",
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",
    "process_madvise",
    "pidfd_getfd",
    "pidfd_send_signal",
    "socket",
    "io_uring_setup",
)

# Sending a signal: allowed only to this process's own pid (argument 0).
SIGNALS = ("kill", "tgkill", "tkill", "rt_sigqueueinfo", "rt_tgsigqueueinfo")

# Acting on a process by pid: allowed only on the caller itself, pid 0.
# The pid's argument index; the rest take it as the first.
BY_PID = {
    "setpriority": 1,
    "ioprio_set": 1,
    "sched_setaffinity": 0,
    "sched_setscheduler": 0,
    "sched_setparam": 0,
    "sched_setattr": 0,
    "prlimit64": 0,
    "migrate_pages": 0,
    "move_pages": 0,
}

# Calls whose first argument says what the pid means. Pid 0 is the caller
# only with the "process" kind: with `PRIO_PGRP` / `IOPRIO_WHO_PGRP` it is
# the caller's process group, with `PRIO_USER` / `IOPRIO_WHO_USER` every
# process of its user, the worker, the other cars and the server included.
# Allowed only with the kind given here.
BY_PID_KIND = {
    "setpriority": 0,  # PRIO_PROCESS
    "ioprio_set": 1,  # IOPRIO_WHO_PROCESS
}


# prctl's option for whether this process may be dumped, which also decides
# whether a process of the same user may open its `/proc/<pid>` files.
PR_SET_DUMPABLE = 4

# prctl's option that Landlock (and seccomp, unprivileged) require first.
PR_SET_NO_NEW_PRIVS = 38


def prctl(option: int, value: int) -> None:
    """`prctl(2)` with one argument, through libc."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(option, value, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl failed")


def make_undumpable() -> None:
    """Keep every other process of this user out of this one's `/proc`
    files: its descriptors, its memory, its environment. A car runs as the
    same user as the server, the worker and the other cars, and would
    otherwise read or write any of them through `/proc/<pid>/fd` or read
    `/proc/<pid>/environ`. Off Linux there is nothing to do."""
    try:
        prctl(PR_SET_DUMPABLE, 0)
    except (OSError, AttributeError):
        pass


def install() -> None:
    """Install the filter on this process, for good.

    Raises whatever stops it: an `ImportError` or `RuntimeError` without
    the bindings or libseccomp, an `OSError` from the kernel. The caller
    treats any of them as a car that must not run.
    """
    import pyseccomp as sc

    pid = os.getpid()
    refuse = sc.ERRNO(errno.EPERM)
    rules: Any = sc.SyscallFilter(sc.ALLOW)
    for name in REFUSED:
        rules.add_rule(refuse, name)
    for name in SIGNALS:
        rules.add_rule(refuse, name, sc.Arg(0, sc.NE, pid))
    for name, arg in BY_PID.items():
        rules.add_rule(refuse, name, sc.Arg(arg, sc.NE, 0))
    for name, kind in BY_PID_KIND.items():
        rules.add_rule(refuse, name, sc.Arg(0, sc.NE, kind))
    rules.add_rule(refuse, "clone")
    rules.add_rule(refuse, "clone3")
    rules.add_rule(refuse, "prctl", sc.Arg(0, sc.EQ, PR_SET_PDEATHSIG))
    rules.load()


# Landlock's syscalls on x86_64 and its constants (linux/landlock.h).
_LANDLOCK_CREATE_RULESET = 444
_LANDLOCK_ADD_RULE = 445
_LANDLOCK_RESTRICT_SELF = 446
_LANDLOCK_CREATE_RULESET_VERSION = 1
_LANDLOCK_RULE_PATH_BENEATH = 1
_READ_FILE = 1 << 2
_READ_DIR = 1 << 3
# The rights a car's ruleset handles, each from the ABI version that
# introduced it: 13 filesystem rights in v1, then REFER (v2), TRUNCATE (v3),
# TCP bind and connect (v4), IOCTL_DEV (v5), and scoping abstract unix
# sockets and signals to the car's own process (v6). A ruleset naming a
# right its kernel does not know is refused with EINVAL.
_FS_RIGHTS = {1: (1 << 13) - 1, 2: 1 << 13, 3: 1 << 14, 5: 1 << 15}
_NET_RIGHTS = {4: 0b11}
_SCOPES = {6: 0b11}


def handled_rights(abi: int) -> tuple[int, int, int]:
    """The filesystem, network and scope masks for Landlock ABI `abi`."""

    def known(rights: dict[int, int]) -> int:
        mask = 0
        for version, bits in rights.items():
            if version <= abi:
                mask |= bits
        return mask

    return known(_FS_RIGHTS), known(_NET_RIGHTS), known(_SCOPES)


class _RulesetAttr(ctypes.Structure):
    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    ]


class _PathBeneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def _syscall(number: int, *args: Any) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    result = int(libc.syscall(ctypes.c_long(number), *args))
    if result < 0:
        code = ctypes.get_errno()
        raise OSError(code, f"syscall {number}: {os.strerror(code)}")
    return result


def library_paths() -> list[Path]:
    """What a car may still read: the directories Python imports the
    standard library, numpy, scipy and cocoracer from, and the shared
    libraries numpy and scipy ship beside themselves."""
    import cocoracer
    import numpy
    import scipy  # type: ignore[import-untyped]

    found = {Path(sysconfig.get_paths()[key]) for key in ("stdlib", "platstdlib")}
    for package in (numpy, scipy, cocoracer):
        directory = Path(package.__file__ or "").parent
        found.add(directory)
        found.add(directory.with_name(directory.name + ".libs"))
    return sorted(p for p in found if p.is_dir())


def restrict_files(readable: list[Path]) -> None:
    """Install the Landlock ruleset on this process, for good: no write
    anywhere, reads only beneath `readable`. Raises `OSError` where the
    kernel has no Landlock, or refuses it."""
    abi = _syscall(
        _LANDLOCK_CREATE_RULESET,
        None,
        ctypes.c_size_t(0),
        ctypes.c_uint32(_LANDLOCK_CREATE_RULESET_VERSION),
    )
    fs, net, scoped = handled_rights(abi)
    attr = _RulesetAttr(handled_access_fs=fs, handled_access_net=net, scoped=scoped)
    size = 8 if abi < 4 else 16 if abi < 6 else 24
    ruleset = _syscall(
        _LANDLOCK_CREATE_RULESET,
        ctypes.byref(attr),
        ctypes.c_size_t(size),
        ctypes.c_uint32(0),
    )
    try:
        for path in readable:
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = _PathBeneath(allowed_access=_READ_FILE | _READ_DIR, parent_fd=fd)
                _syscall(
                    _LANDLOCK_ADD_RULE,
                    ctypes.c_int(ruleset),
                    ctypes.c_int(_LANDLOCK_RULE_PATH_BENEATH),
                    ctypes.byref(rule),
                    ctypes.c_uint32(0),
                )
            finally:
                os.close(fd)
        prctl(PR_SET_NO_NEW_PRIVS, 1)
        _syscall(_LANDLOCK_RESTRICT_SELF, ctypes.c_int(ruleset), ctypes.c_uint32(0))
    finally:
        os.close(ruleset)


def _fork_and_exit() -> None:
    if os.fork() == 0:  # only if the filter let it through
        os._exit(0)


def _refused(call: Any) -> bool:
    try:
        call()
    except (PermissionError, RuntimeError):  # RuntimeError: no new thread
        return True
    return False


def check() -> str | None:
    """Install the sandbox in a throwaway child and check it holds there.

    Returns None when it does, or what went wrong. The child reports on a
    pipe, so this process is never sandboxed itself.
    """
    readable = library_paths()
    read, write = os.pipe()
    pid = os.fork()
    if pid == 0:  # the child: report and exit, never return
        os.close(read)
        try:
            install()
            restrict_files(readable)
            import colorsys  # noqa: F401  a later import still reads the library

            problems = [
                what
                for what, call in (
                    ("fork", _fork_and_exit),
                    ("a thread", lambda: threading.Thread(target=print).start()),
                    ("socket", socket.socket),
                    ("kill the parent", lambda: os.kill(os.getppid(), 0)),
                    ("reading /etc/passwd", lambda: open("/etc/passwd").close()),
                )
                if not _refused(call)
            ]
            report = ", ".join(f"{p} was allowed" for p in problems)
        except BaseException as exc:
            report = f"{type(exc).__name__}: {exc}"
        os.write(write, report.encode() or b"ok")
        os._exit(0)
    os.close(write)
    with os.fdopen(read, "rb") as pipe:
        report = pipe.read().decode(errors="replace")
    os.waitpid(pid, 0)
    return None if report == "ok" else report or "the check's child died"


def main() -> int:
    problem = check()
    if problem is not None:
        print(
            f"cocoracer-server sandbox: the car sandbox does not work: {problem}",
            file=sys.stderr,
        )
        return 1
    print("cocoracer-server sandbox: the car sandbox works")
    return 0


if __name__ == "__main__":
    sys.exit(main())
