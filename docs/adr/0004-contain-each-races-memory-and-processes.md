# Each race's memory is metered per car, and no process outlives its race

Partly superseded by [0007](0007-one-process-per-car.md): each car's process caps its own address space, which replaces the per-call meter, the worker's ceiling and the known holes below; and the cars' seccomp filter refuses new processes. The group kill and the sweep stay.

ADR 0002 left the race worker with no resource limits: it defended against
a controller that hangs and nothing else. Memory turned out to be the other
failure that stops the event rather than one team. A controller that
allocates without bound pushes the service's cgroup to `MemoryMax`, and the
kernel's OOM killer then kills the whole race, or, before ticket 05 set
`OOMPolicy=continue`, the whole service with it. Either way sixteen teams
lose a race to one team's bug.

The goal is that a hog faults only its own car. Every controller in a race
shares one worker process, so nothing outside the process can tell whose
memory is whose. The answer is to turn "too much memory" into an exception
inside the hog's own call, because the engine already faults just that car
on any exception from a controller.

So `race_worker` wraps every team's controller in a meter. Around the
import, `reset` and every `step`, it reads the process's peak RSS
(`getrusage`, one syscall) before and after and charges the growth to that
car. Past **512 MB per car** (`MEMORY_BUDGET_MB`) the wrapper raises a
`ControllerFault` whose message is the fault the team reads, in French. The
car DNFs, and the controller and its module's globals are dropped so the
rest of the race can reuse the memory. An import that alone goes over the
budget leaves the car unloadable with the same fault.

The meter only sees memory after the fact, so one giant allocation would
get past it. For that, `race_worker.main()` sets `RLIMIT_AS` before any team
code loads: the worker may grow by **2 GB** (`ADDRESS_SPACE_HEADROOM_MB`)
beyond its size once its own imports are done. A giant allocation then
raises `MemoryError` inside the hog's `step()`, which the meter turns into
the same fault. The cap is headroom rather than an absolute size because
the worker's startup address space is mostly OpenBLAS's per-core thread
reservations: 1.5 GB on the 16-core laptop it was measured on, against a
normal 16-car live race that grows by about 125 MB on top.

## Considered options

- **One process per controller** (rejected for now): the right answer. Each
  car would get its own `RLIMIT_AS` and its own OOM kill, and both holes
  below would close. But the engine steps every car in one loop and hands
  each a laser scan every tick, so splitting it is an IPC protocol, a
  per-tick round trip for sixteen processes, and days of work.
- **A transient systemd scope per race** (`systemd-run --scope -p
  MemoryMax=`, rejected): it protects the server, which ticket 05's
  `oom_score_adj` and `OOMPolicy=continue` already do, but still kills the
  whole race for one hog.
- **`RLIMIT_AS` alone** (rejected): a hog that grows a little each step
  reaches the ceiling at a random moment, as likely inside the engine or
  another car's `step()` as its own, and then the wrong car is faulted or
  the race is void.
- **Meter per call, under a ceiling** (chosen).

## Consequences

- Teams have a documented budget: the start kit's `tuto.md` tells them
  each car has 512 MB. `MEMORY_BUDGET_MB` and that sentence change together.
- Memory grown by a controller's own background thread is charged to
  whichever car happens to be stepping when it shows up. Innocent cars
  can be faulted for it; spawning threads is already outside what a
  controller is expected to do.
- Peak RSS only rises. Memory a faulted hog gave back can be taken again by
  another car without being charged until that car pushes past the old
  peak, so two hogs in one race can together get further than one budget.
  The ceiling still bounds them.
- A hog that trips the ceiling while the engine, not a controller, is
  allocating voids the race with a `MemoryError`. The server survives it
  (ticket 05), and the race is simply one fewer in the standings.
- The meter costs two `getrusage` calls per car per tick, about 1300
  syscalls a second for a full field, which is nothing next to the laser
  scan.

## Processes

The other thing a race could leave the event with is processes. Nothing
needs a controller to start one, but nothing stops it: `os.fork`,
`os.posix_spawn` and `os.system` all work in the worker, and the push
analysis only refuses the obvious `subprocess`. Before this, the runner
killed only the worker, and only on a timeout, so every child a controller
started outlived its race inside the service's cgroup. A `sleep 3600` holds
one of the unit's `TasksMax=256` slots, and a few dozen races of it leave no
slot for the next worker, voiding every race after; a CPU burner slows
every race after it. A spawned child also inherits the worker's pipes, so
the runner, which waited for them to close, took a normal race for a hang.

So no process outlives its race:

- The worker is started with `start_new_session=True`, so it leads its own
  process group, which everything a controller forks or spawns joins.
- The runner waits for the worker to *exit*, not for its pipes to close:
  they are drained on threads, and the exit is watched through a pidfd,
  which leaves the worker unreaped.
- On every race end, normal, timed out or failed, `os.killpg` sends SIGKILL
  to the whole group. The worker is still unreaped at that point, so the
  group's id cannot have been reused.
- Then the sweep. Races run one at a time, so once a race's group is dead,
  every process left in the unit's cgroup but the server itself is a
  leftover. The runner reads the unit's `cgroup.procs` (the path comes from
  `/proc/self/cgroup`) and SIGKILLs every PID but its own, which catches a
  child that left the group with `setsid`. It cannot leave the cgroup:
  `ProtectControlGroups=yes` makes the hierarchy read-only to the unit. A
  non-zero sweep is logged with the number it killed.

The sweep kills everything in a cgroup but one PID, so it runs only where
that cgroup is certainly the unit's: `INVOCATION_ID` set (systemd started
us) *and* our cgroup named `cocoracer-server.service`, the unit `deploy.sh`
installs. `INVOCATION_ID` alone is not enough, because a desktop session's
systemd can pass it to every shell of a terminal. A dev run or a test run
is never in that cgroup, so the sweep there does nothing, and the group
kill is the whole of the protection.

### Considered options

- **Group kill alone** (rejected): `setsid` escapes it in one line.
- **A transient systemd scope, or a cgroup, per race** (rejected): the
  thorough answer, but the unit runs unprivileged with a read-only cgroup
  hierarchy, so it needs `Delegate=` and a cgroup manager in the server, or
  D-Bus access to systemd from a sandboxed unit. Races run one at a time,
  so the unit's own cgroup already is the per-race cgroup, minus the server.
- **A seccomp filter against `fork`/`execve` in the worker** (rejected for
  now): it would stop the spawning itself, but the numpy/OpenBLAS threads
  and the live view make a filter that allows threads and refuses
  processes fiddly to get right, and the kill already bounds the damage to
  one race.
- **Group kill, then a sweep of the unit** (chosen).

### Consequences

- A process a controller starts lives at most as long as its race. During
  the race it can still take CPU and task slots from the other cars.
- The sweep relies on races running one at a time (the scheduler's race
  lock). Were two races ever to run at once, the sweep of one would kill
  the other's worker.
- The sweep never runs off the deployed unit. A dev run can still leak a
  `setsid` escapee, which is accepted: only the event runs hostile code.
