# Every car runs in its own sandboxed process, stepped in parallel

Supersedes, in part, [0002](0002-race-in-a-killable-subprocess.md) (what
can void a race), [0004](0004-contain-each-races-memory-and-processes.md)
(the per-call memory meter, the worker's address-space ceiling, and how a
controller's processes are stopped) and
[0005](0005-a-per-step-timer-faults-a-hanging-car.md) (the `SIGALRM`
timer).

Every controller used to run in the one race worker process, so a
controller that stopped that process stopped the race for the whole field:
`sys.exit()` in `step`, `os._exit`, a SIGKILL to itself or to the worker, a
replaced `SIGALRM` handler that disarmed the step timer, a write to the
result descriptor, a patch to the engine or another car. No `except`, and
no list of banned imports, closes all of those. The goal is that a
controller that misbehaves gets its car DNF'd and the race goes on for
everyone else; what it imports does not matter.

So the race worker forks one child per car (`car_process`, the car's
side; `car_field`, the worker's). The child
sandboxes itself, imports only its team's file, and answers one call at a
time on a pipe: the import, `reset`, every `step`. The worker sees a reply,
a reply it cannot read, a closed pipe, or no reply by the deadline; all
but the first DNF the car with a French reason and kill its process.

## How

- **Deadlines on the pipe.** The import has 10 s, `reset` 5 s, `step` 1 s,
  each counted from the call's send, with the same French faults as ADR
  0005's timer. No signal is involved, so a C loop holding the GIL, or a
  controller swallowing every exception, is stopped all the same.
- **Parallel steps.** The cars are an adapter behind cocoracer's field
  seam (`car_field.CarField`, cocoracer's ADR 0007): it sends every
  running car its step, `collect`s every reply (each against its own
  deadline), and hands the outcomes back; cocoracer's engine judges them
  and applies the commands in grid order, as for controllers in its own
  process. A race comes out bit-identical to the in-process engine (a test
  checks it), so it stays deterministic apart from the wall-clock faults.
  The imports and the resets go in parallel too. When this landed the
  worker overrode the engine's private controller phase with a copy
  (`ParallelEngine`) and copied its fault rule; the seam replaced both.
- **An average budget.** Parallel steps make a tick cost the slowest car,
  so a car whose `step()` averages more than 20 ms over its last 160 steps
  (4 s of race, counted only while it is stepped) is DNF'd: "le contrôleur
  a pris en moyenne plus de 20 ms par step". 20 ms is the 25 ms tick less
  the engine's ~4 ms and the pipe. Measured by the worker, from send to
  reply, so a car cannot misreport it.
- **seccomp** (`sandbox.py`, PyPI's `pyseccomp` over the system
  libseccomp). Default allow; refused with `EPERM`, so the car sees a
  `PermissionError`: signals to any pid but its own, `clone` and `clone3`
  outright (one thread per car, decided by the maintainer: a thread fails
  to start with Python's `RuntimeError: can't start new thread`; BLAS is
  single-threaded already and the car's own code starts none), `fork`,
  `vfork`, `execve`, `ptrace`, `process_vm_readv/writev`,
  `process_madvise`, `pidfd_getfd`, `pidfd_send_signal`, `socket`,
  `io_uring_setup`, the by-pid `setpriority`, `ioprio_set`, `sched_set*`,
  `prlimit64`, `migrate_pages` and `move_pages` on anyone but itself, and
  `PR_SET_PDEATHSIG`. "Itself" is pid 0, and for `setpriority` and
  `ioprio_set` only with the per-process kind: pid 0 with `PRIO_PGRP` or
  `PRIO_USER` (`IOPRIO_WHO_PGRP`, `IOPRIO_WHO_USER`) means the car's process
  group or every process of its user. Until the 2026-10-07 review the
  filter checked only the pid and left those two kinds open, so a car
  could renice the worker, its siblings and the server, and `ioprio_set`
  was not filtered at all.
- **Landlock** (`sandbox.restrict_files`, three syscalls through ctypes).
  No file written, created or removed anywhere; files read only beneath
  the standard library's directories and numpy's, scipy's and cocoracer's
  package directories (and their `.libs`), which is all a later import
  needs (decided by the maintainer: cars read and write no files). Not the
  state directory, the private `/tmp`, the race's directory, `/etc`. Where
  the kernel's ABI has them, TCP and signals or abstract sockets beyond the
  car's own process are refused too. Installed before the team's file is
  imported, not after as the maintainer allowed, so the module body is
  confined as well: the team's source is served to the import and to
  `linecache` from memory, since the car may not read even its own file.
- **Fail closed.** A car whose sandbox does not install, or whose process
  dies while setting it up, never loads its file and is DNF'd ("le serveur
  n'a pas pu isoler le contrôleur, qui n'a pas couru"), the cause on the
  worker's stderr, which the runner relays to the journal. The deploy runs
  `python -m cocoracer_server.sandbox` and stops if the sandbox does not
  work, and the unit adds `SystemCallFilter=seccomp landlock_create_ruleset
  landlock_add_rule landlock_restrict_self`: `@system-service` leaves them
  out, and without them the car's first such call kills it with SIGSYS.
  Checked with a full worker race under `systemd-run --user` with the
  unit's syscall filter, `NoNewPrivileges`, `RestrictAddressFamilies`,
  `PrivateTmp`, `ProtectSystem=strict`, `MemoryMax` and `TasksMax`: every
  hostile car DNF'd with its reason, the race not void; and with the extra
  `SystemCallFilter` line removed, every car DNF'd with the sandbox fault.
- **The analysis no longer refuses network or process modules.** A team can
  import `socket`, `subprocess`, `ctypes`; the call fails when made.
- **No reading the others.** The worker reads the request in two parts and
  forks the cars after the first, so no car's memory ever held another's
  source. Each car's import names a symlink the worker makes to
  `/proc/self/fd/5`, which holds a memfd with the car's own source, so in
  any car it resolves to that car's source and never another's. The worker, every car and the server
  are non-dumpable, so no process of the same user can open their
  `/proc/<pid>/fd`, `mem` or `environ` (the admin password is in the
  server's; the worker is also started without it). Yama's
  `ptrace_scope=1` and the filter stop `ptrace` and its relatives.
- **Memory.** The budget stays 512 Mo (`MEMORY_BUDGET_MB`, `MEMORY_FAULT`).
  Each car caps its own `RLIMIT_AS`, soft and hard, at its address space
  after the imports plus 512 MB: an allocation past it raises
  `MemoryError` in the controller, and the car is DNF'd. The worker sets
  one BLAS thread before numpy is imported (the cars inherit it), which
  keeps that start near 250 MB rather than 1.5 GB. Each car sets
  `oom_score_adj` 1000, the worker 500, so should the unit reach
  `MemoryMax` the kernel kills a car first, then the worker, never the
  server. No `_forget_module`, no peak-RSS meter.
- **Logs.** Each car gathers what it prints with the log's own rules and
  hands the lines back with each reply; the worker stamps them with the
  call's race time and checks them against the same rules (a reply larger
  than the remaining budget allows is a forged one). A raise also leaves
  its traceback, the team's frames only, in the log.

## Measured (16-core Intel Core Ultra 7 255H laptop, 31 GB)

- Starting 16 cars: fork 65-70 ms; fork, import and reset 115-125 ms.
  Spawning 16 fresh interpreters that import cocoracer took 1.1 s, in
  parallel, and would have started each car with nothing to share. Fork it
  is.
- IPC per tick, 16 cars: about 1 ms headless (4.4 ms a tick against 3.3 ms
  in one process, cars that return at once). Paced at 40 Hz the numbers are
  noisy with the laptop's frequency scaling: 7.8-8.1 ms a tick against
  7.8 ms in one process for cars that return at once, 9.4-10.6 ms against
  6.7 ms for the four baselines. A car's round trip, send to reply, is
  0.4-0.7 ms, most of it waking the car up. Either way well inside the
  25 ms tick.
- Memory: one car's process is 58 MB resident, almost all of it pages
  shared with the worker; its proportional share is about 10 MB and its
  private dirty memory 7 MB after 400 ticks. A live 16-car race grew the
  machine's used memory by about 350 MB, the worker's own 117 MB included;
  the server is 120 MB. So a full field takes about 0.5 GB of the unit's
  `MemoryMax=4G`, which leaves room for about six cars at their whole
  budget; `MemoryMax` stays 4G. Past it, cars are killed first.
- `calibrate` on a local dev server (not the deployed unit, which was
  running the old code alongside), 16 cars at 2 and 4 ms per step, 1 and
  49 viewers: whole-race RTF 1.000 in all four, worst 10 s window 0.996,
  no void (the single process voided at 2 ms: 0.687 and 0.587).

## Cores

Parallel steps assume a core per car. This laptop has 16 cores (6
performance, 8 efficient, 2 low-power; no hyper-threading), which ran the
deployed service's `calibrate`. On a machine with fewer cores than cars the
cars share them: a tick costs about the field's total step time divided by
the cores, still far better than the sum the single process paid. But a
car's measured step time then includes waiting for a core, so with many
heavy cars on few cores an innocent car could be charged other cars' work
by the average budget. With 16 cars and 16 cores this does not arise; on
a smaller event machine, measure before the event.

## Considered options

- **Catch `BaseException` in the worker** (rejected): closes `sys.exit`,
  nothing else.
- **Ban more imports in the analysis** (rejected): `np.os`, `getattr` with
  a built string and the like reach around any list.
- **A distinct uid per car** (rejected): needs root or `CAP_SETUID` in a
  unit that has no capabilities.
- **A user and PID namespace per car** (rejected): more moving parts; a new
  PID namespace only applies to children, so each car needs one more fork.
- **Spawn each car fresh** (rejected): 1.1 s to start a field, against
  70 ms forked, and more memory per car. Forking needs the two-part request
  so no car inherits another's source.
- **One process per car, forked, with seccomp and Landlock** (chosen).

## Consequences

- No controller can void a race any more. The voids left are the
  server's: no entries, the worker killed or failing, the machine itself
  falling behind real time. The runner's wall-clock budget stays for those;
  its void reason quotes the worker's last stderr line, or its exit code.
- A call that overruns loses what it printed during that call: the car's
  process is killed before it can reply.
- Inside the unit, a syscall that systemd's own filter refuses (outside
  `@system-service`, such as `ptrace`) kills the car with SIGSYS rather than
  raising `PermissionError`: still a DNF of that car alone.
- What remains shared: the CPU. A car has one thread and its step's
  deadline; the average budget DNFs one that slows the race. The process
  sweep of ADR 0004 stays, though a car can no longer start a process.
- `start_kit/wtf.md` tells teams the car reads and writes no file, starts
  no thread or process and opens no connection, and that imports still
  work; the analysis's `open` warning says the same.
- ADR 0004's known holes (a background thread charged to another car,
  peak RSS shared between hogs, a hog tripping the ceiling inside the
  engine) are closed: each car's memory is its own process's.
