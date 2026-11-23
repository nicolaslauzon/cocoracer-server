<h1 align="center">CS Games Ulaval qualification, Fall 2026</h1>

<p align="center">
  <img src="img/qualification_progression.png" width="1000" alt="Qualification score of each team over the 79 qualification races">
</p>

## Results

The official score is the sum of a team's finishing positions in the five
official races, and the lowest wins. The qualification score is the number of
overtakes in the last eight qualification races, and the highest is best.
Each team name links to its code.

| Rank | Team                                            | Official | Qualification |
| ---: | ----------------------------------------------- | -------: | ------------: |
|    1 | [VoitureDeLaMortQuiTue](VoitureDeLaMortQuiTue/) |       10 |            42 |
|    2 | [casseur2q](casseur2q/)                         |       15 |            55 |
|    3 | [number1](number1/)                             |       19 |            14 |
|    4 | [Flash McDouble](Flash_McDouble/)               |       21 |            34 |
|    5 | [mcqueen](mcqueen/)                             |       24 |            19 |
|    6 | [CarGoatSlayer99](CarGoatSlayer99/)             |       25 |             7 |
|    7 | [CoralieDube](CoralieDube/)                     |       36 |             3 |
|    8 | [Les Patates Chaudes](Les_Patates_Chaudes/)     |       37 |             0 |
|    9 | [Bb Antoine](Bb_Antoine/)                       |       43 |             1 |
|   10 | [Les balles](Les_balles/)                       |       45 |             0 |

<p align="center">
  <img src="img/official_progression.png" width="800" alt="Cumulative official score of the ten teams after each official race">
</p>

## Pépites

Some of the code the teams pushed, quoted as written.

`Les_balles` subtracts from a local variable, so the car never slows down:

```python
def slow(slef, speed):
    speed -= 5.0
```

`Flash_McDouble` has this docstring on its speed function, and a commented-out
call to a method with a matching name. Its pushes are described "Milou est un
génie", "Vincent est un génie" and "Je suis attardé".

```python
""" 8=========D bite """
#left_side, right_side = self.fonction_penis(laser_scan)
```

`VoitureDeLaMortQuiTue` has four front lasers named RAGE:

```python
vision_front_RAGE_01 = laser_scan[71]
vision_front_RAGE_02 = laser_scan[70]
```

`Bb_Antoine` computes a speed in each branch, then always returns 12:

```python
return 12, self.to_radian(steering_angle)
```

`number1` keeps a commented-out `# SAFE` copy of its code, identical to the
code right below it. It pushed 92 versions.

`Les_Patates_Chaudes` left a test in the pushed file:

```python
c.track_info = TrackInfo("yo", 5, 20, [P(n / 10, 0) for n in range(20)])
```

`mcqueen` prints this on every step:

```python
print("LAMINE")
```

`casseur2q` starts its nearest-point search from a large number:

```python
best_delta = 9999999999999
```

`CarGoatSlayer99` meant to test both lasers against 10, but the first one is
only tested for being non-zero:

```python
elif laser_scan[18] and laser_scan[54] < 10.0:
```

`CoralieDube` has nothing to quote: its controller is 34 clean lines, after 53
pushes.

## The code

Each team folder has every version the team pushed (`v1.py`, `v2.py`, ...) and
`latest.py`, the last one. [`index.tsv`](index.tsv) lists every push with its
time and the description the team gave. The code is published as the teams
wrote it, unused code and comments included, and the teams are its authors
([MIT](LICENSE)). `number1` links the
[F1TENTH course kit](https://f1tenth-coursekit.readthedocs.io/) in its
comments as a source for part of its approach.

Run one with the [start kit](../start_kit/) and the simulator in
[`cocoracer/`](../cocoracer/).
