"""Regenerate `replay_fixture.json`, a short recorded race for the replay player.

Three baselines on the stadium, in race mode, for 20 s of simulated time
including the countdown: pure pursuit and the disparity extender drive
cleanly, and the open-loop stub plows into the first hairpin so the
recording also carries the paused, ghost and DNF states (its crash budget
is lowered to two for that). The controllers run in this process, but the
frames are the race worker's own `Recorder`'s, so the fixture has the
format the server records and `static/replay.js` reads;
`tests/test_runner.py` checks a fresh recording against it.

Run from the repo root:

    PYTHONPATH= server/.venv/bin/python server/tests/data/make_replay_fixture.py
"""

import json
from pathlib import Path

from cocoracer.config import DEFAULT_PARAMS, load_config
from cocoracer.controller import load_controller
from cocoracer.engine import RaceEngine
from cocoracer.track import build_track

from cocoracer_server.race_worker import Recorder

HERE = Path(__file__).resolve().parent
CONTROLLERS = HERE.parents[2] / "cocoracer" / "controllers"
OUTPUT = HERE / "replay_fixture.json"

DURATION = 20.0
ENTRIES = [
    ("pure pursuit", "pure_pursuit.py"),
    ("disparity", "disparity_extender.py"),
    ("open loop", "open_loop.py"),
]


def main() -> None:
    config = load_config(DEFAULT_PARAMS)
    config.race.max_crashes = 2
    track = build_track(config.tracks["stadium"])
    controllers = [
        load_controller(CONTROLLERS / file, baselines=config.baselines)
        for _, file in ENTRIES
    ]
    names = [name for name, _ in ENTRIES]
    engine = RaceEngine(
        track, config, controllers, names, mode="race", auto_start=False
    )
    # As the worker races: started, then recorded from the release on.
    engine.start()
    recorder = Recorder(engine)
    while not engine.finished and engine.time < DURATION - 1e-9:
        engine.tick()
        recorder.observe()
    replay = recorder.replay()
    OUTPUT.write_text(json.dumps(replay, separators=(",", ":")), encoding="utf-8")
    frames = len(replay["frames"])
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes, {frames} frames)")


if __name__ == "__main__":
    main()
