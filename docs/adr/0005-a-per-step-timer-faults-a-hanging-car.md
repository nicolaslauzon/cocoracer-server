# A per-call timer faults a hanging car, and the budget stays as backstop

Superseded by [0007](0007-one-process-per-car.md): the limits and their faults are unchanged, but each is a deadline on the car's pipe, and an overrun kills the car's process. There is no `SIGALRM` timer any more, and no hang the deadline cannot stop.

ADR 0002 contained a controller that never returns from `step()` by killing
the whole race process on a wall-clock budget and voiding the race. That
keeps the event going, but it costs every team the race for one team's
`while True:`. It rejected the in-process alternative, a per-step timer,
because signals do not work on Windows, cannot interrupt a C-level call, and
would put event concerns inside the library teams run.

Those objections are weaker than they were. The race worker is server-side
and Linux-only, and it already wraps every team's controller in a meter that
faults only the memory hog (ADR 0004). A timer fits in the same wrapper,
outside cocoracer.

So `race_worker` arms `signal.setitimer(ITIMER_REAL, limit, 0.05)` around
every call into team code, with a limit per kind of call:

- every `step()`: **1 s** (`STEP_TIME_LIMIT`), fault "le contrôleur n'a pas
  répondu en 1 s";
- `reset()`, once before the countdown: **5 s** (`RESET_TIME_LIMIT`),
  generous because it is where a controller warms up (imports, JIT, a
  first inference); fault "le contrôleur n'a pas répondu en 5 s dans
  reset()";
- the import of the team's file, its module body: **10 s**
  (`LOAD_TIME_LIMIT`), fault "le contrôleur n'a pas été chargé en 10 s".

The `SIGALRM` handler raises `_StepOverran` inside the call, wherever it
is: a Python loop, a `time.sleep`, or the next bytecode after a C call
returns. `_Metered` (for `reset` and `step`) and `_load` (for the import)
turn it into the fault the team reads, in French, and drop the controller;
the engine DNFs that car as it does any raising controller, or the car
starts unloadable. The race goes on.

`_StepOverran` is a `BaseException`, so a controller's own `except
Exception:` does not swallow it. The timer repeats every 50 ms
(`TIMER_REPEAT`) until the call returns, so an interrupt that is lost
(raised inside a `__del__` or a weakref callback, where Python only prints
it) or swallowed once by a bare `except:` is raised again. The timer is
cleared, and the handler disarmed, the moment the call returns.

## Considered options

- **Keep voiding the race** (rejected): one team's bug costs sixteen teams.
- **A watchdog thread** (rejected): it cannot interrupt the main thread any
  better than a signal, and it adds a thread to every race.
- **A per-step `SIGALRM` timer in the worker** (chosen).

## Consequences

- What a signal cannot interrupt still stalls the process: a long C call
  holding the GIL, a controller that catches every repeat of the interrupt
  and loops on, or one that installs its own `SIGALRM` handler.
  Those fall to ADR 0002's wall-clock budget and void the race, as before.
  The budget stays.
- Signals are delivered to the main thread only. The worker runs the race
  loop on its main thread and the live view on its own threads,
  so every `step()` is on the main thread.
- A slow but finite controller, under a second but over the 25 ms tick,
  is not this timer's concern: it slows the live race, and that is
  event-readiness 04.
