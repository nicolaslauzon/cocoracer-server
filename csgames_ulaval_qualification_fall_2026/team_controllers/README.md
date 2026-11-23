# Team controllers

The controllers that ten teams pushed to the server during the
competition, on 8 and 9 October 2026. Each team wrote its own controller. The
teams are the authors of the code in their folder.

Each folder has every version the team pushed (`v1.py`, `v2.py`, …) and
`latest.py`, the last one. `index.tsv` lists every push with its time and the
description the team gave.

Run one with the [start kit](../../start_kit/) and the simulator in
[`cocoracer/`](../../cocoracer/).

| Folder | Versions |
| --- | --- |
| `Bb_Antoine` | 32 |
| `CarGoatSlayer99` | 34 |
| `casseur2q` | 58 |
| `CoralieDube` | 53 |
| `Flash_McDouble` | 61 |
| `Les_balles` | 46 |
| `Les_Patates_Chaudes` | 49 |
| `mcqueen` | 56 |
| `number1` | 92 |
| `VoitureDeLaMortQuiTue` | 41 |

The code is published as the teams wrote it, including unused code and
comments. `number1` links to the
[F1TENTH course kit](https://f1tenth-coursekit.readthedocs.io/) in its
comments, as a source for part of its approach.

## License

MIT, see [LICENSE](LICENSE). The copyright belongs to the teams.

## Pépites

Some favorites from the code and the push descriptions. All of it is quoted
as the teams wrote it.

- **`Les_balles`** has a `slow(slef, speed)` method that does `speed -= 5.0`.
  `speed` is a local float, so the car never slows down. The controller also
  sets `speed = 15.0` and then checks `if speed == 0.0`.
- **`Bb_Antoine`** computes `self.speed` in both branches of its steering
  logic and then returns the constant `12` as its speed. Version 13 is
  described as "calisse".
- **`number1`** keeps a commented-out copy labelled `# SAFE` right above the
  live code. The two are identical. The team pushed 92 versions.
- **`Flash_McDouble`** imports `randint;` with a semicolon and never calls it.
  The docstring of `get_nextStep_tangeant` says "cette fonction retourne des
  trucs". The docstring of `getSpeed` is `""" 8=========D bite """`, and a
  commented-out line calls a method named `fonction_penis`. The function
  builds a `devant` variable and never reads it. Pushes are described
  "Randomize!", "Milou est un génie", "Vincent est un génie", "Je suis
  attardé" and "Paranthèse mal mise".
- **`VoitureDeLaMortQuiTue`** names its front lasers `vision_front_RAGE_1`,
  `vision_front_RAGE_2`, `vision_front_RAGE_01` and `vision_front_RAGE_02`.
  `vision_front_RAGE_02` and `vision_gauche_min` both read `laser_scan[70]`.
  `self.tick` is incremented on every step and never read.
- **`Les_Patates_Chaudes`** ends with a `__main__` block that builds a
  `TrackInfo("yo", ...)`. Above it sit about 30 lines of commented-out
  steering, including a block pasted twice and an `if`/`elif` pair that
  assign the same `0.01`.
- **`mcqueen`** prints `"LAMINE"` on every step. Version 35 is described as
  "lamien".
- **`casseur2q`** starts its search for the nearest centerline point with
  `best_delta = 9999999999999`, and its first push is described "best
  voiture".
- **`CarGoatSlayer99`** turns by 65 or 90 degrees, and ends with
  `elif laser_scan[18] and laser_scan[54] < 10.0`, which only tests the
  second laser.
- **`CoralieDube`** has no pépite. The latest controller is 34 lines of
  clean, readable code with no typo, no dead branch and no unused variable.
  Fifty-three pushes and nothing to quote. Version 13 is described "Test
  disparity compile ?", which is as close as it gets.
