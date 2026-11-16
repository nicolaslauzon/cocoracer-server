# Two-tier scoring, with a randomly shuffled qualification grid

The challenge scores teams twice. Qualification races, held automatically
every five minutes, award one point per position gained and nothing for
positions lost; that accumulated score seeds the starting grid for the three
admin-triggered official races, which are scored on the sum of the
finishing positions, lowest wins (amended 2026-10-01: it was the mean; a
missed official race counts as that race's field size plus one). The
qualification grid is **shuffled at random before every race** rather than
ordered by qualification score.

The random grid is the part worth recording, because ordering the grid by
score is the obvious design and it is broken. Positions gained is a
*relative* measure, so coupling the grid to it creates a feedback loop. If
the best score starts at the front, the leader can gain nothing and scores
zero from then on while everyone behind climbs past them in points: the table
becomes a rubber band that oscillates and converges, and after thirty races
the seeding it feeds into the official races is close to noise — which
defeats the only purpose it has. If the best score starts at the back, the
loop runs the other way and the strongest cars snowball away from a field
they also have to overtake sixteen-deep in a single lap.

Shuffling breaks the coupling. Positions gained keeps the properties that
make it good for this event — it rewards overtaking, it is trivial to
explain, and a crash costs a team nothing beyond the places it loses, so
nobody is knocked out of contention by one bad race — while every team sees a
fair spread of good and bad starts across roughly thirty races. What is
given up is the narrative of climbing the grid as you climb the table.

Amended 2026-10-08: a qualification pays one point per **pass** rather
than per position gained, and the score sums only the event's last eight
qualifications. A pass is one car's lead over another, in distance along
the track, climbing through a whole number of laps, so lapping a car counts
and a pass taken back cancels; a race's total is still floored at zero.
Without lapping this is exactly positions gained, so the argument above
holds unchanged. The window lets the board follow the cars as teams improve
them over the afternoon, instead of being locked in by early races.

## Considered options

- **Grid ordered by qualification score** (rejected): the intuitive design,
  and the one that motivated the feedback-loop analysis above.
- **Classic finishing points** (`field size − finish position`), grid ordered
  by score: no degeneracy, and keeps the grid-climbing narrative, but it
  punishes a single bad race hard and rewards holding position over
  attacking.
- **Random grid, positions gained** (chosen).

## Consequences

- The qualification score means "overtook a lot across the afternoon", not
  "is fastest". A quick car that qualifies well but starts mid-pack by luck
  still scores; a car that starts on pole by luck scores nothing that race.
- Nothing in the server may order the qualification grid by anything. The
  shuffle is injected into `Scheduler` so tests can make it deterministic,
  and that seam is the only place the grid order is decided.
- The two scores answer different questions and are shown as two separate
  tables, not combined into one number.
