# hoopsim

A basketball league simulator in the spirit of management sims. Pure Python 3.10+,
no dependencies.

```bash
python -m hoopsim new --seed 1 --teams 12 --out league.db   # generate, save, print summary
python -m hoopsim game --seed 1 --pbp                       # one game: play-by-play + box score
python -m hoopsim season --seed 1                           # full season: standings + leaders
python -m hoopsim.calibrate --seasons 3                     # compare sim averages with the NBA
python -m unittest discover -s tests -t .                   # run tests
```

## Data model (`hoopsim/models.py`)

```
League ── rules: LeagueRules (cap, roster limits, quarter length, shot clock, fouls)
   ├── teams:   {id: Team}   ── tactics: Tactics (pace, 3PT rate, scheme, starters, minutes)
   ├── players: {id: Player} ── attributes, hidden, physique, contract, injury
   └── games:   {id: Game}   ── result: GameResult, box_score: BoxScore (per-player lines)
```

**Player**
- `Attributes`: 20 visible 1–20 ratings (scoring, playmaking, defence,
  rebounding, physical, mental). 10 is a league-average pro.
- `HiddenAttributes`: consistency, clutch, injury proneness, work ethic,
  professionalism, ambition, loyalty. These feed the engine and development, not the UI.
- `current_ability` (CA, 1–200) is *derived*: a position-weighted mean of the
  attributes (`POSITION_WEIGHTS`). `potential_ability` (PA) is the hidden ceiling.
- `ability_at(position)` applies a familiarity penalty when a player is out of position.
- `Physique` (height, wingspan, weight) and `Contract` (salary, expiry, option).

**Team**: identity, conference/division, reputation, budget, and `Tactics`.
The roster is not stored on the team; it is every player whose `team_id` matches.
That gives one source of truth, so trades and signings can't desync.

**League** owns everything and enforces the rules: `sign`, `release`, `trade`,
`payroll`, `cap_space`, `standings`, and `validate()`.

## Generation (`hoopsim/generate.py`)

Players come from a position template plus height effects (taller players are
better at boards and blocks, worse at handling and speed), scaled to hit a target CA.
That target is PA × an age development curve, which peaks at 27. Ageing vets lose
physical attributes first. Salaries follow a steep curve on CA. Rosters are filled
by a snake draft with noisy scouting, so there are strong and weak teams without
absurd gaps. `auto_lineup` (in `lineup.py`) brute-forces the best starting five and
shares out 240 minutes. The schedule is a repeated round robin with alternating
home and away.

## Storage (`hoopsim/db.py`)

SQLite with one JSON blob per entity, plus indexed columns for common queries:

```sql
SELECT json_extract(data, '$.attributes.three_point') AS tp, last_name
FROM players WHERE position = 'SG' ORDER BY tp DESC LIMIT 10;
```

## Match engine (`hoopsim/engine.py`)

Possession by possession, about 11 ms per game:

```
clock -> turnover? (handler security vs defenders' hands, press/zone)
      -> non-shooting foul? (bonus free throws from the 5th team foul)
      -> shooter (weighted by scoring skill) -> zone: rim / close / mid / three
         (player tendencies x team 3PT rate x offensive focus x defence scheme)
      -> block? -> shooting foul? (and-ones) -> make? (shooter vs matchup defender
         and rim protector, home edge, clutch, form) -> assist
      -> miss: rebound battle (rating + height + crash-the-glass) -> putback or reset
```

- **Matchups**: the lineup is held in PG..C slots and each slot guards the same
  slot. Switching defences create random mismatches.
- **Fatigue**: energy drains on court (faster with low stamina or a press) and
  recovers on the bench and between periods. All ratings scale with energy.
- **Coach AI**: substitutions at dead balls, driven by each player's minutes target
  and energy, with minimum stint lengths, foul-trouble benching, fouling out,
  closing lineups in close fourth quarters, and benches in blowouts.
- **Hidden attributes**: consistency sets game-to-game form; clutch shifts late shooting.
- **Score effects**: leaders ease off and trailers push, which keeps margins realistic.
- **End-of-game strategy** (4th quarter and OT):
  - A trailing defence fouls to stop the clock. It always fouls when down 1–3 with
    the shot clock off, and fouls when down 4+ inside 45s while the deficit is still
    catchable. It fouls with role players and hunts poor free-throw shooters.
  - The NBA last-two-minutes penalty applies: the second foul in the last two
    minutes puts a team in the bonus.
  - A leader milks the shot clock, then dribbles out the game once the shot clock is off.
  - A trailing team hurries, needs a three when down 3, and plays for the tying two
    when down 2. A tied team holds for the last shot.
  - Crunch-time lineups bring on free-throw shooters to protect a lead and three-point
    shooters to chase one.
- **End of quarters**: two-for-ones, holding for the last shot (a tougher,
  isolation-heavy look), and buzzer heaves whose odds depend on the time left and
  whether the ball has to go full court.
- **Timeouts**: 7 per game and 2 per overtime. They're called to stop 8-0 runs and,
  late in close games, to advance the ball and set a play. They allow substitutions
  and a short rest.
- All base rates live in `Tuning`. `skill_impact` controls how much talent gaps matter.

`simulate_game` is pure (it doesn't change the league). `play_game`, `play_day` and
`play_season` store results and box scores, which persist in the save file.

### Calibration

`python -m hoopsim.calibrate` simulates whole seasons and compares them with
2023-24 NBA per-team-game averages. All 17 tracked stats are currently within
tolerance. The average winning margin is 11.5 points (NBA: about 12), and
5.7% of games go to overtime (NBA: 6.2–6.4%).

What it took to get overtime right (it was 2.2%):
- **Tough last shots.** Held-for last shots face a set defence: an isolation shot
  mix and a 0.70 make factor. Trailing teams' set plays get a milder 0.92. Tied
  teams win on the last possession about a third of the time; most games go to OT.
- **Realistic heaves.** Full-court heaves after a rebound are much harder than
  frontcourt heaves after a timeout.
- **Fouling timing.** Teams down 4+ defend while the shot clock still forces a shot,
  and only foul once it's off.
- **Crunch-time compression.** In the last 5 minutes of a single-digit game the
  score effect is 5x stronger: leaders play not to lose, trailers take risks.

Known gaps:
- **Guards rebound a little too little.**
- **No in-game injuries.**

## Next

Season loop: injuries, player development and ageing, morale, contracts and
free agency, the draft, AI trades, playoffs.
