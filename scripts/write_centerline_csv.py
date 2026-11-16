"""Write the event track's centerline into the start kit.

Run from the repo root with the server's venv, which has both cocoracer and
the server's choice of event track:

    server/.venv/bin/python scripts/write_centerline_csv.py

`server/tests/test_start_kit.py` fails when the file is stale.
"""

from pathlib import Path

from cocoracer.config import DEFAULT_PARAMS, load_config
from cocoracer.controller import centerline_csv
from cocoracer.track import build_track

from cocoracer_server.scheduler import DEFAULT_TRACK

OUT = Path(__file__).resolve().parents[1] / "start_kit" / "centerline.csv"


def event_centerline_csv() -> str:
    """The CSV of the track the event races on."""
    spec = load_config(DEFAULT_PARAMS).tracks[DEFAULT_TRACK]
    return centerline_csv(build_track(spec))


def main() -> None:
    OUT.write_text(event_centerline_csv(), newline="\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
