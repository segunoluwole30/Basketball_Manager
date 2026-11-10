# hoopsim

A basketball league simulator in the spirit of management sims. Pure Python 3.10+,
no dependencies.

## Play

```bash
python -m hoopsim play                  # new league: pick a team
python -m hoopsim play --team HCM       # new league as the Harbor City Mariners
python -m hoopsim play --load my.db     # carry on a saved game
```

You're the general manager. During the season:

| Command | What it does |
|---|---|
| `sim [N]`, `week`, `deadline`, `end` | play N days, a week, to the trade deadline, or the rest of the season |
| `next` | after the regular season: playoffs, awards, then the offseason |
| `roster [ABBR]`, `player ID` | ratings, scouted potential, contracts, injuries, morale; full scouting report |
| `lineup`, `start A B C D E`, `minutes ID=MIN`, `auto on/off` | set the starting five (PG..C) and minute targets, or let your assistant |
| `tactics pace=16 three=14 defense=zone focus=perimeter crash=8 depth=9` | team tactics |
| `fa`, `sign ID SALARY YEARS`, `release ID` | free agents and what they want; sign (salary in $M) or waive |
| `trade 12,15 for ABBR 40` | propose a trade; the other team decides by its own priorities |
| `finances`, `standings`, `leaders`, `schedule`, `results`, `history`, `save` | |

In the offseason you're asked about each decision for your team: team options,
re-signing (paying over the ask helps), draft picks from your scouts' board,
free-agent bids each round (weaker teams have to outbid stronger ones), trade
offers from AI teams, and roster cuts. Answer `auto` to hand any of them to
your assistant.

You never see everything. Potential is shown as your scouts' estimate with a
range. The range narrows as a player ages and builds a track record, and it's
tighter for your own players. Hidden attributes show up as scouting notes
("injury prone", "big-game player", "lazy in practice"). Past 29, potential
just reads "peaked".

The AI never makes your team's decisions. Everything goes through a `Manager`
(`hoopsim/manager.py`): the CLI asks you, and `AIManager` is the autopilot.
The only exception is a roster below the league minimum, which gets
minimum-salary fills. Tests use a manager that refuses everything to prove it.

## Watch: spatial prototype (`hoopsim/spatial/`)

```bash
python -m hoopsim watch                          # 40 half-court possessions, opens in your browser
python -m hoopsim watch --home HCM --away IFF    # pick the teams
python -m hoopsim watch --load my.db             # your team vs its next opponent
python -m hoopsim.spatial.measure                # compare with NBA half-court numbers
```

A second, experimental engine in which every player has a position and moves ten
times a second. It's half-court only for now: no transition and no substitutions.
`watch` writes a self-contained page (`hoopsim_watch.html`): a to-scale court,
players and ball, scoreboard and shot clock, commentary, play-by-play, and
pause/speed/skip controls.

- **Offence**: four-out spacing (five-out when the centre can shoot). The ball
  handler re-evaluates every few tenths of a second: shoot, drive, pass, call a
  pick-and-roll, or hold. Each option is scored by expected points from the current
  positions, and smarter players pick the best option more reliably.
- **Defence**: man-to-man with reaction delays set by basketball IQ. On-ball
  defenders anticipate drives. Off-ball defenders stay close one pass away and sink
  from the weak side. A rim protector meets drivers, bigs play drop coverage on
  pick-and-rolls (or switch, per tactics), and screens physically hold defenders.
- **Outcomes**: make chance comes from distance, the shooter's rating and the
  nearest defender's distance, height and quality. The same positions drive blocks,
  shooting fouls, steals in passing lanes, dribble turnovers, charges and
  rebounds with box-outs.
- **Speed**: about 1.5 ms per possession (a game in about 0.3 s), fast enough for
  whole seasons.

### Learned decision values (`values.py`, `train.py`)

Each option a ball handler has (shoot, drive, continue a drive, pass to each
teammate, pick-and-roll, hold) is described by a few features: shot clock,
expected points of the shot, how open a receiver will be after the pass flies,
interception risk, defender gap, crowd in the paint, and so on. Its value is a
weighted sum. The starting weights reproduce the original hand-written formulas,
and training refits them so each value predicts the points the possession
actually went on to score:

```bash
python -m hoopsim.spatial.train                 # 5 rounds x 20,000 possessions, ~2.5 min
python -m hoopsim.spatial.measure               # learned values (default)
python -m hoopsim.spatial.measure --values heuristic
```

Each round plays possessions with the current values, tries a random option 10–25%
of the time so every option gets tested, records every decision with the
possession's eventual points, and refits by least squares (pulled toward the
previous weights). It trains on leagues 100–102 and is tested on leagues 0–2. It's
deterministic, and the results are stable across extra rounds.

What it learned:
- A shot is worth about 0.9–1.05 × its expected points + 0.2–0.4 (the
  offensive-rebound and foul value of a miss).
- Interception risk dominates the value of a pass.
- Drives are worth more the closer they start and when the defender is beaten.
- A crowded paint matters less than the hand-written formula assumed.

It also exposed loopholes in the physics. The first trained players stopped
passing (turnovers fell to 4%), because dribbling under pressure was nearly risk-free
and pull-up jumpers were as good as catch-and-shoot ones. Both are now modelled:
pressure turnovers, a catch-and-shoot bonus and an off-the-dribble penalty
(NBA: ~37% vs ~34% from three). Receivers' defenders now break on passes.

### Inside game

- **Cuts:** off-ball players cut when their defender loses them, most often for
  smart players, poor shooters and players whose man is sagging. Passes lead a
  moving receiver.
- **Lobs:** thrown over the defence only to athletic cutters or rollers (6'9"+
  with good leaping). They're contested at the catch rather than in the passing
  lane, and finished as alley-oops.
- **Dunker spot:** a centre who can't score in the post waits under the rim. Any
  catch within 8 ft of the rim is finished with a gather step.
- **Post-ups:** a big who can score inside sets up on the block, backs his man
  down, and shoots hooks or turnarounds. The strength battle affects the make
  chance, and strong post players draw double-teams that open kick-outs.
- **Defence:** a "tagger" picks up open cutters and rollers in the paint.

Held-out leagues (3,000 possessions each, learned values), with the numbers
before the inside game in brackets:
- **Shot mix:** threes 51–65% of shots (68–78%; NBA ~41%), rim 18–28% (7–13%;
  NBA ~30%).
- **Rim shots come from:** drives ~55%, cuts ~14%, putbacks ~13%, post-ups ~9%,
  alley-oops ~5% and dunker-spot finishes ~4%.
- **Also in range:** assists 0.60–0.63 per make, alley-oops 1–2 per 100
  possessions, FG% by zone, offensive rebounds and free-throw rate. 7–8 of 14
  stats are within tolerance (was 5–7).
- **Still off:** 1.23–1.27 points per possession (target ~0.98), turnovers ~8%
  (target 13%), almost no mid-range, and possessions a little short.

## Simulation commands

```bash
python -m hoopsim new --seed 1 --teams 12 --out league.db   # generate, save, print summary
python -m hoopsim game --seed 1 --pbp                       # one game: play-by-play + box score
python -m hoopsim season --seed 1                           # season + playoffs, awards, leaders
python -m hoopsim career --seasons 20 --seed 1              # 20 years: drafts, free agency, ageing
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

## Season loop (`hoopsim/season.py`)

```python
league = generate_league(seed=1)
rng = random.Random(1)
summary = play_full_season(league, rng)   # regular season, playoffs, awards
report = run_offseason(league, rng)        # everything between seasons
```

**During the season**
- **Injuries** happen in games. The risk rises with court time, fatigue and the
  hidden injury-proneness rating. Injuries range from day-to-day knocks to
  season-ending tears, and about 1.2 players per team are out on a typical day.
  A player listed "out N games" misses exactly his team's next N games.
- **Coaches re-plan rotations** when players get hurt or come back.
- **Morale** updates weekly from playing time versus role, winning (weighted by
  ambition) and injuries. It feeds game-day form and re-signing decisions.
- **Trade deadline** at 60% of the season.
- **Playoffs** (`playoffs.py`): the top two thirds of each conference (rounded down
  to a power of two) are seeded by record, then point differential. Best-of-7
  series use 2-2-1-1-1 home court, and the conference champions meet in the finals.
- **Awards**: MVP, Finals MVP, Defensive POY, Rookie of the Year, Sixth Man and Most
  Improved. Each player's season line (stats, age, CA) goes into `player.career`.

**Offseason**, in order:
1. **Retirements**: likelier with age and declining ability, and much likelier
   for players nobody signed.
2. **Development** (`development.py`): growth peaks at 19–22 and declines from about
   30, with athleticism going first. It's scaled by work ethic, minutes played and
   room to potential (never above PA). Professionalism slows decline.
3. **Options**: teams decline overpaid team options; underpaid players opt out.
4. **Re-signing**: teams keep rotation players they can afford. Contenders will
   pay some tax. Players re-sign based on loyalty, morale and team quality.
5. **Draft** (`draft.py`): a 4-pick lottery for non-playoff teams weighted toward
   the worst, then 2 rounds. Teams scout potential through noise. First-rounders
   get a rookie scale deal with a team option; undrafted players become free agents.
6. **Free agency** (`market.py`): rounds of bids under a simplified soft cap (cap
   space, one mid-level exception, minimum deals). Players take the best mix of
   money and team quality, and lower their asks round by round.
7. **Trades**: contenders value ability now; rebuilders value 3-year projections
   and cap relief. Trades happen only when both sides gain.
8. **Roster cuts and fills** to 13–15, a new schedule and fresh lineups.

Box scores are summarised into career lines at the end of each season and then
dropped, so save files stay small over decades.
