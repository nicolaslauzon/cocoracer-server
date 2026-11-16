# Every race runs in a subprocess the server can kill

Partly superseded by [0007](0007-one-process-per-car.md): every car runs in a process of its own, so no controller can void a race; the budget and the void stay for the worker's own failures.

Partly superseded by [0004](0004-contain-each-races-memory-and-processes.md): races now have a memory budget and a process sweep.

Partly superseded by [0005](0005-a-per-step-timer-faults-a-hanging-car.md): a hang a signal can interrupt now faults only its car; the budget and the void stay for the rest.

The cadence is now two minutes (`scheduler.QUALIFICATION_INTERVAL`), not the five this record assumes; a race's one-second start cost still does not matter against it.

The server runs untrusted student code with no sandbox, no resource limits
and no import allowlist — a deliberate choice for a friendly competition on a
LAN. The one failure it does defend against is a controller that never
returns from `step()`, because that is the only one that stops the event
rather than one team.

There are two distinct failure modes and they need different answers. A
controller that **raises** is contained inside the engine: the vehicle is
DNF'd with `CONTROLLER_FAULT`, the race carries on, and a team running
locally sees the same clean DNF instead of a traceback out of the race loop.
A controller that **hangs** — `while True:`, or an honest loop over the
five-thousand-point centerline every tick — cannot be rescued from inside the
process at all. A 40 Hz loop calling it simply never comes back, and with it
the five-minute cadence, the leaderboards, and the afternoon.

So the server spawns `python -m cocoracer_server.race_worker` per race, hands
it the field as JSON on stdin, and reads the classified result from stdout
with a wall-clock budget of the race's own time limit plus a grace margin. If
the budget passes, the parent kills the process and records the race as
**void**: it is kept in the history with its reason, it scores nothing for
anybody, and the scheduler starts the next race on its normal boundary.

## Considered options

- **In-process with a per-tick timer** (rejected): signal-based timeouts are
  unreliable under a tight numeric loop, do not work on Windows, and cannot
  interrupt a C-level call; it also puts event-operations concerns inside a
  library that teams run on their laptops.
- **A thread per race** (rejected): Python cannot kill a thread, so a hung
  controller would leak a live thread per race for the rest of the event.
- **Docker per race** (rejected for now): the right answer if the server were
  a machine worth protecting, but it buys isolation this threat model does
  not need, at the cost of a container runtime on the event laptop.
- **Subprocess with a wall-clock budget** (chosen).

## Consequences

- A void race is a real outcome the scoring has to tolerate, so
  `scoring_records()` filters void races out and the standings simply have
  one fewer race in them.
- The child cannot share objects with the parent, so the field crosses the
  boundary as JSON — team name and source text — and the result comes back
  as plain data. That makes the race genuinely reproducible from a record.
- A race's cost is now a process spawn plus an interpreter start, roughly a
  second. Against a five-minute cadence this does not matter.
- The engine keeps no knowledge of the event: containment of a *raising*
  controller is a cocoracer concern, containment of a *hanging* one is the
  server's.
