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

**Player instructions.** Every player has tendencies (1–20, 10 = no preference):
`three`, `mid`, `drive`, `post`, `pass` and `cut`. They follow his skills, with
some personality: some players love a shot they aren't great at. Change them with
`instruct ID three=16 post=4`; the player card shows them. Tendencies change what a
player *chooses*, not how well it goes. In the spatial engine they're a style
preference added to each option's learned value, about 0.25 points at 20. In the
season engine they shape his shot mix, usage and passing.

**Tactics** in both engines:

| Tactic | Effect |
|---|---|
| `three` (rate) and `focus` | A team-wide shot preference. Perimeter focus spaces five-out; inside focus puts both bigs on the blocks and favours post entries. |
| `pace` | Shorter setup and earlier shots. |
| `crash` | How many players attack the offensive glass. |
| `defense` | `zone` packs the paint and concedes perimeter looks. `press` forces turnovers but gets beaten off the dribble more. `switch` swaps on screens. `man` plays drop coverage. |

Same two teams with different instructions (spatial engine): perimeter focus with
three-point rate 20 takes 61–65% threes; inside focus with rate 1 takes 35–41% and
nearly doubles post-ups. A press roughly doubles a ball-handling opponent's
turnovers. A mid-range tendency of 20 more than doubles that player's two-point
jumpers.

You never see everything. Potential is shown as your scouts' estimate with a
range. The range narrows as a player ages and builds a track record, and it's
tighter for your own players. Hidden attributes show up as scouting notes
("injury prone", "big-game player", "lazy in practice"). Past 29, potential
just reads "peaked".

The AI never makes your team's decisions. Everything goes through a `Manager`
(`hoopsim/manager.py`): the CLI asks you, and `AIManager` is the autopilot.
The only exception is a roster below the league minimum, which gets
minimum-salary fills. Tests use a manager that refuses everything to prove it.

## Web interface

```bash
pip install -r requirements-web.txt     # fastapi, uvicorn (+ httpx for the API tests)
python -m hoopsim web                   # starts a local server and opens your browser
python -m hoopsim web --load my.db --port 8000 --no-open
```

The same game as `play`, as a management UI in the browser: one league per server,
saved to SQLite files in the folder you start it from (`Save`/`Load` in the sidebar,
including half-way through an offseason). The simulator itself still needs no
dependencies; only the web server does, and the built front end ships inside the
package (`hoopsim/web/static/`), so Node isn't needed to play.

| Screen | What's on it |
|---|---|
| **Home** | record, conference standings, next games and recent results, team leaders, injuries, and an inbox: results, injuries, trades, offers from AI teams (accept or decline), season news. The top bar sims a day, a week, to the trade deadline or to the end of the regular season, then runs the playoffs (a day, a round or all). |
| **Squad** | sortable roster tables (overview, ratings, stats, contracts) for any team, finances. Click any name for the player card: attributes, scouted potential with its range, scouting notes, ability by position, player instructions, season and career stats. |
| **Tactics** | starting five by position (with each player's ability at that slot), minutes that must total 240 (with a "balance" helper), the assistant-coach toggle, team tactics and player instructions. |
| **Transfers** | free agents with their asks and a contract form that shows how you'd pay (cap space, mid-level exception, minimum); a trade builder with live salary-matching and roster feedback and the other team's verdict. |
| **League** | standings, leaders, schedule and results with box scores (current season), a daily scoreboard, the playoff bracket and past seasons. |
| **Offseason** | one screen per step: team options, re-signing (asks and the chance he stays), the draft board pick by pick, every free-agency round, trade offers and roster cuts. Any decision, a whole step or the rest of the summer can be handed to your assistant. |

The offseason runs as resumable steps (`start_offseason`, `offseason_pending`,
`answer_offseason` in `season.py`); `run_offseason` drives the same steps with a
`Manager`, so the terminal game and the AI behave exactly as before. The API is
under `/api` (interactive docs at `/api/docs`).

Front-end development (React + TypeScript + Vite, source in `web/`):

```bash
cd web && npm install
npm run dev        # http://localhost:5173, proxies /api to `python -m hoopsim web --no-open` on :8000
npm run build      # type-checks and writes the bundle to hoopsim/web/static/ (commit it)
```

## Watch: spatial prototype (`hoopsim/spatial/`)

```bash
python -m hoopsim watch                          # 40 half-court possessions, opens in your browser
python -m hoopsim watch --home HCM --away IFF    # pick the teams
python -m hoopsim watch --load my.db             # your team vs its next opponent
python -m hoopsim.spatial.measure                # compare with NBA half-court numbers
```

A second, experimental engine in which every player has a position and moves ten
times a second. `watch` shows half-court possessions; `watch --full` plays whole
games with transition and substitutions (see *Full games* below).
`watch` writes a self-contained page (`hoopsim_watch.html`): a to-scale court,
players and ball, scoreboard and shot clock, commentary, play-by-play, and
pause/speed/skip controls.

- **Offence**: four-out spacing (five-out when the centre can shoot). The ball
  handler re-evaluates every few tenths of a second: shoot, drive, pass, call a
  pick-and-roll, or hold. Each option is scored by expected points from the current
  positions, and smarter players pick the best option more reliably.
- **Defence**: man-to-man with reaction delays set by basketball IQ. On-ball
  defenders anticipate drives and play up on shooters. Off-ball defenders stay close
  one pass away and sink from the weak side. Closeouts depend on the shooter. A rim
  protector meets drivers, help defenders dig at the ball, bigs play drop coverage
  on pick-and-rolls (or switch, per tactics), and screens physically hold defenders.
- **Outcomes**: make chance comes from distance, the shooter's rating and the
  nearest defender's distance, height and quality *at release*. The same positions
  drive blocks, shooting fouls, steals in passing lanes, dribble turnovers, charges
  and rebounds with box-outs.
- **Speed**: about 1.5 ms per possession (a game in about 0.3 s), fast enough for
  whole seasons.

### Full games (`fullgame.py`, `transition.py`)

```bash
python -m hoopsim watch --full                   # a whole spatial game in the viewer (~2.5 MB page)
python -m hoopsim watch --full --quarter 4       # just the 4th quarter (5 = first overtime)
python -m hoopsim.spatial.measure --games 60 --jobs 2   # full-game numbers against NBA averages
```

`FullGame` wraps the half-court engine in a 48-minute game and returns the season
engine's `GameSim` (result, box score with minutes, points, shooting, rebounds,
assists, steals, blocks, turnovers, fouls and plus-minus, play-by-play), so spatial
games could later be stored like season games. It isn't wired into the season loop yet.

- **Clock.** A period clock runs through every possession. The shot clock carries
  over (14 after an offensive rebound or a non-shooting foul) and is off when less
  time is left than it shows. The period ends at the buzzer, even mid-possession
  (a shot already in the air counts). As in the NBA, the game clock keeps running
  through the inbound after a made basket, except late in periods. Jump balls start
  the game and each overtime, and possessions alternate between quarters.
- **Transition** (`transition.py`). After steals, defensive rebounds and some made
  baskets, the players run the floor at ten updates a second (the viewer's camera
  pulls back to show it). A team pushes depending on how it got the ball, its pace
  and whether anyone is already behind the defence. The defence turns and runs
  back after a delay (longer for the team that just lost the ball): the deepest
  defender takes the rim, the next stops the ball, and the rest pick up the most
  dangerous men. Handlers throw ahead, drop the ball off to the rim runner when the
  last defender commits, and kick out to open shooters. Then the half-court engine
  takes over from those positions with the shot clock running and scrambled
  matchups: a clear lane starts as a drive, an advantage as an immediate decision,
  and no advantage flows into the half-court offence against a defence that isn't
  set yet. Ahead passes can be picked off, and a team out of time heaves from
  where it is.
- **Fouls.** Shooting fouls and charges come from the half-court engine.
  Non-shooting fouls happen during possessions. The bonus starts with the 5th team
  foul of a period (or the 2nd in its last two minutes), and players foul out at 6.
- **Fatigue and substitutions.** Energy drains with how far a player runs (and
  faster against a press) and recovers on the bench, at period breaks and in
  timeouts. Tired players are slower, react later and shoot worse. Substitutions
  happen at dead balls (after made baskets only late in games) and use the season
  engine's logic: the rotation plan from `lineup.plan_rotation` (or the coach's
  minutes), fatigue, foul trouble, and the best players in close games and the
  bench in blowouts. Fouled-out players leave at once.
- **End of game.** Trailing teams foul to stop the clock and call a late timeout to
  advance the ball. Leading teams milk the shot clock and dribble out the end. Teams
  hold for the last shot of a quarter, go two-for-one, and look for a three when
  down three. These are passed to the half-court decisions as small value biases.

Measured over 60 games (seed 0; per team-game), with the calibrated half-court
defence, against 2025-26 Basketball-Reference league averages (transition and
minutes are estimates; sources in `measure.py`):

| | Sim | NBA |
|---|---|---|
| Transition share of possessions | 14.9% | ~15% (est.) |
| Transition points per possession | 1.23 | ~1.26 (est.) |
| FTA per 100 possessions | 27.1 | 23.6 |
| Personal fouls per 100 possessions | 19.3 | 20.0 |
| Starters' minutes (by rank) | 34.4, 32.6, 30.5, 28.2, 23.4 | ~30–36 (est.) |
| Pace | 135 | 99.4 |
| Points per team | 152 (113 per 100) | 115.6 (115.8 per 100) |
| Turnovers per 100 possessions | 10.2 | 14.6 |

Points per 100 possessions, transition, fouls and minutes are about right. Pace is
far too high: half-court possessions use about 5.3 s of game clock after the
bring-up, against about 11 s in the NBA, so teams get ~135 possessions a game and
the scoring leader averages ~40 points. Teams use about 11 players a game because
high totals make blowouts (garbage time) common.
Full games take about 0.65 s each.

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

### Pull-ups

A ball handler at the arc can take one or two hard dribbles into a mid-range
jumper. It's valued like any other shot (off the dribble, defender trailing), so
mid-range specialists take it and most players don't.

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

When the inside game was added, held-out leagues (3,000 possessions each, learned
values) gave these numbers, with the numbers before it in brackets:
- **Shot mix:** threes 51–65% of shots (68–78%; NBA ~41%), rim 18–28% (7–13%;
  NBA ~30%).
- **Rim shots come from:** drives ~55%, cuts ~14%, putbacks ~13%, post-ups ~9%,
  alley-oops ~5% and dunker-spot finishes ~4%.
- **Also in range:** assists 0.60–0.63 per make, alley-oops 1–2 per 100
  possessions, FG% by zone, offensive rebounds and free-throw rate. 7–8 of 14
  stats are within tolerance (was 5–7).
- **Still off:** 1.23–1.27 points per possession (target ~0.98), turnovers ~8%
  (target 13%), almost no mid-range, and possessions a little short.

### Defence

Every option was worth too much (a three about 1.1 points, a possession about
1.25), so the defence was too weak. Instrumenting it showed where:
- **Jumpers had no release.** A shot went up the instant the handler chose it, and
  the contest was judged from where defenders stood at that moment, so a closeout
  had no time to arrive. Now a mid-range or three-point jumper takes about 0.3 s to
  get off (a little longer off the dribble, quicker for good shooters). The shooting
  motion cues nearby defenders to get a hand up. The make chance, blocks and fouls
  use their positions at release.
- **Closeouts ignored the shooter.** A closing defender stopped 3 ft from everyone,
  and on the ball backed off to 3 ft, so a catch-and-shoot three from a swing pass
  was the best shot in the game (about 1.24 points). Now defenders run good shooters
  off the line (arm's length, about 1.5–2 ft) and give poor shooters room so they
  can't drive by.
- **A tight contest barely hurt a three.** NBA tracking puts very tightly contested
  threes about ten points below wide-open ones. The engine took off seven. The full
  contest now takes ten, and a wide-open three is a point better.
- **Missing turnovers.** Help defenders now dig at drivers who come within reach.
  Passes can be thrown away or fumbled without a steal: more often with a hand in
  the passing window, on long or lob passes, and from poor passers. This chance is
  part of the pass's risk, so decisions account for it. Any defender with his feet
  set in the driver's path can take a charge, once per drive (it used to be only
  help defenders, with a fresh chance every tick). Drivers and post players
  sometimes travel or commit offensive fouls.

Held-out leagues 0–2 (3,000 possessions each, values retrained after each change):

| stat | before | after | NBA target |
|---|---|---|---|
| points per possession | 1.27–1.31 | 1.15–1.20 | ~0.98 |
| turnovers | 6–7% | 8–10% | ~13% |
| threes, share of shots | 63–67% | 57–58% | ~41% |
| rim, share of shots | 16–19% | 18–21% | ~30% |
| mid-range, share of shots | 3–6% | 5–8% | ~13% |
| 3P% | 37–39% | 31–36% | ~36% |
| assists per make | 0.51–0.65 | 0.44–0.60 | ~0.60 |
| stats within tolerance | 6–7 of 14 | 5–7 of 14 | |

Lost-ball and offensive-foul turnovers are now near NBA rates (per 100
possessions: digs ~1.2, charges ~0.7, travels and offensive fouls ~0.5).

- **Tried and dropped:**
  - Letting defenders intercept passes in flight and cheat into passing lanes.
    Turnovers barely moved: the offence stopped passing (0.5–0.8 passes per
    possession) and assists fell out of range.
  - Top-locking great shooters, with backdoor cuts as the counter. Not better.
  - Making passes past the on-ball defender safer. It brought more passes and
    assists, but threes rose to 62–65%.
- **Training noise:** retraining the same physics with different training seeds
  moves held-out points per possession by up to ±0.03. Treat smaller differences
  as noise.
- **Still off:**
  - Points per possession are about 1.18. With the targets' FG% by zone, offensive
    rebounds and free-throw rate, ~1.10 is about the floor (0.98 is a half-court
    number, but those FG% are close to full-game ones).
  - Turnovers are ~9%. About 5% of passes are turned over, against ~2–3% in the
    NBA, so the offence makes too few passes (~0.8 per possession) and possessions
    are short (~10.8 s).
  - Each turnover risk added to drives pushed shots back toward threes. Threes are
    still ~57% of shots, because cuts, rolls and lobs make few rim shots. That gap
    is on the offensive side.

### Measuring against the right yardstick

`measure` now uses one consistent set of NBA **half-court possession** targets
(see the docstring in `hoopsim/spatial/measure.py` for sources). The old 0.98
points target was Cleaning the Glass's points per half-court *play*, which
restarts after an offensive rebound. Per possession, the way this engine counts,
the half court is worth about **1.10**. The old mid-range target (13%) also
double-counted floaters: long mid-range is ~7% of shots, and short mid-range
(4–14 ft) ~23%. The source tables couldn't be opened from the cloud environment,
so the numbers come from cached snippets and are approximate.

Two quick experiments ruled out suspected causes of the excess scoring:
- **Starters only:** lineups mixed from each team's rotation by minutes scored the
  same, within noise. The engine judges offence against defence, so bench units
  play about as efficiently as starters.
- **Player ratings:** generated players are, if anything, slightly worse three-point
  shooters than real ones (expected ~31–32% against ~36%).

Held-out leagues against the new targets (seeds 0 / 1 / 2):

| | Sim | NBA half court |
|---|---|---|
| Points per possession | 1.16 / 1.20 / 1.18 | ~1.10 |
| eFG% | .494 / .519 / .538 | ~.527 |
| Threes with a defender within 2 ft | 52% / 35% / 53% | ~2% |
| Wide-open threes (6+ ft) | 10% / 14% / 12% | ~44% |
| Seconds per possession | 10.7–10.8 | ~15.5 |
| Passes per possession | 0.7–0.9 | ~3.2 |
| Turnovers | 8–10% | ~13% |
| Rim share | 18–21% | ~28% |

Update after reworking contests and ball movement:
- **Contest geometry.** Defenders keep realistic distances: about 3.5 ft off a
  perimeter ball handler, closeouts that stop at about 3 ft on shooters (more on
  non-shooters), and a hand-up that closes at most about 2.5 ft during the
  shooting motion. Before, any defender within 8 ft ran to 1.2 ft.
- **Help and movement.** A perimeter defender stunts at drives, leaving his man
  for the kick-out. Weak-side defenders sink deeper. Passes are safer (about NBA
  rates), and pick-and-rolls are easier to call.

| | Before | After | NBA half court |
|---|---|---|---|
| Threes with a defender within 2 ft | 35–53% | 6–13% | ~2% |
| Wide-open threes | 10–14% | 19–21% | ~44% |
| 3P% | 31–36% | 36–38% | ~36% |
| eFG% | .49–.54 | .52–.54 | ~.53 |
| Passes per possession | 0.7–0.9 | 1.3 | ~3.2 |
| Points per possession | 1.16–1.20 | 1.20–1.23 | ~1.10 |
| Threes, share of shots | 57–58% | 65–71% | ~42% |
| Rim share | 18–21% | 11–15% | ~28% |

Shooting accuracy and how contested shots are are now realistic, and the ball
moves more. The shot mix got worse, though, and the reason is now measured:
**drives die**. Teams drive about 0.5 times per possession (near the NBA), but only
19% of drives end in a shot at the rim, because help always arrives in time.
Pick-and-rolls are still rare (0.05 per possession). With a weak inside game, a
realistic perimeter game makes threes the best option. The next engine work is
drive success against help (late or bad rotations, finishing through contact,
dump-offs), and pick-and-rolls worth running.

Inside-game diagnosis and pick-and-roll rework:
- **Drives are fine.** About 61% reach within 6 ft, and they end roughly like NBA
  drives: ~53% in a shot by the driver, ~31% kick-outs, ~10% dump-offs. Drives
  produce about 0.12 rim attempts per possession, as in the NBA.
- **The missing rim attempts are everything else:** cuts, putbacks, post-ups,
  lobs and dump-offs total ~0.06 per possession against ~0.13 in the NBA.
- **Pick-and-rolls were genuinely bad here.** Forced-exploration tests showed
  possessions with one scoring 0.94 points against 1.05 without, so the learned
  values correctly avoided them. Fixes: the nearer big sets the screen, the
  roller dives at once, the handler comes off downhill to the nail and reads the
  big every beat, short lobs go over drop coverage, and rollers and cutters
  finish from up to ~11 ft. The gap shrank from 0.11 to 0.05 points, but
  pick-and-rolls are still only 5–7% of possessions.

After retraining (held-out leagues): rim share 15–18% (from 11–15%), threes
64–68%, but points per possession 1.24–1.27 and eFG .55–.58 (higher than
targets). The pattern across several hand-tuned rounds: this defence can contain
the ball, protect the rim, stunt, tag rollers *and* close out on every kick-out,
which real defences can't. Each single change gets rebalanced by the learned
decisions. The next step is **automated calibration**: a search over the few
physics settings behind that trade-off (help timing, closeout speed, stunt depth,
contest strength, rebounding edge, turnover rates), scoring each candidate by
quick retraining against the half-court targets.

The scoring gap is small. What's wrong is the *structure*: defenders get far
too close on threes (the make chance is generous on contested shots to
compensate), and possessions are short and pass-light. Fixing realistic contest
distances and ball movement comes before tuning the totals.

### Automated calibration (`params.py`, `calibrate.py`)

```bash
python -m hoopsim.spatial.calibrate --log calibration.jsonl     # ~45 min on 3 cores
python -m hoopsim.spatial.calibrate --log calibration.jsonl --resume
python -m hoopsim.spatial.measure --params calibrated_params.json
```

The settings behind the rim-versus-arc trade-off now live in one dataclass,
`SpatialParams`. It covers help timing and depth, defender reaction, stunt and
tag distances, drop depth, sag, closeout gap and speed, contest reach and
strength, finishing and three-point adjustments, foul rate, turnover rates and
the rebounding edge. `HalfCourtGame(params=None)` uses the defaults.
`LEGACY_PARAMS` holds the hand-set values from before calibration, and a test
checks that they still play exactly as before.

A candidate can't be judged with decision values trained for different physics,
because the offence would keep taking the shots that used to be good. So each
candidate is retrained (3 rounds × 6,000 possessions, warm-started from the
saved values) on the training leagues 100–102 and then measured there on 9,000
possessions. Its score is the weighted mean of ((sim − target) / tolerance)².
Points per possession, eFG, the shot mix and 3P% carry the most weight. Seconds
and passes carry little, because these settings barely move them.

The search runs in three phases:
1. **Noise:** the defaults are evaluated with 6 different seeds.
2. **Sample:** 90 Latin-hypercube samples over 28 parameters.
3. **Refine:** a (1+3) evolution strategy. Mutants that beat the incumbent are
   re-run with a fresh seed and must still win on the mean.

It uses 3 worker processes. Every evaluation is logged to JSONL, and the search
resumes from that log. The held-out leagues 0–2 are never used during the search.

**Noise:** for the old defaults, the score is 6.90 ± 1.19 (sd across seeds).
Per metric the sd is: points per possession .010, eFG .007, rim share .019,
three share .038 (the retrained policy wobbles), 3P% .004.

**Result:** the Latin-hypercube phase found the basin, with the best sample
scoring 1.54 against the defaults' 6.9. Refinement accepted one step (mean 1.16
over two seeds), and then 20 generations brought nothing beyond noise. The
values were then fully retrained (`train.py` defaults) and measured on the
held-out leagues:

| held-out seed 0 / 1 / 2 | before | after | target |
|---|---|---|---|
| points per possession | 1.19 / 1.24 / 1.25 | 1.17 / 1.19 / 1.16 | 1.10 ± .04 |
| eFG | .528 / .543 / .566 | .515 / .536 / .521 | .527 |
| rim share | .17 / .15 / .15 | .26 / .28 / .31 | .28 |
| close share | .13 / .10 / .10 | .23 / .20 / .23 | .23 |
| mid share | .06 / .08 / .07 | .06 / .10 / .07 | .07 |
| three share | .65 / .68 / .69 | .46 / .42 / .40 | .42 |
| FG% rim | .66 / .64 / .67 | .62 / .64 / .63 | .64 |
| 3P% | .350 / .363 / .382 | .360 / .380 / .342 | .362 |
| threes within 2 ft | .06 / .13 / .06 | .02 / .04 / .05 | .02 |
| wide-open threes | .18 / .19 / .20 | .21 / .26 / .24 | .44 |
| turnovers | .086 / .083 / .082 | .073 / .077 / .084 | .13 |
| offensive rebounds | .26 / .28 / .24 | .22 / .21 / .20 | .27 |
| FTA / FGA | .16 / .14 / .14 | .24 / .23 / .25 | .24 |
| assists per make | .58 / .70 / .68 | .42 / .53 / .51 | .60 |
| passes per possession | 1.2 / 1.4 / 1.3 | 0.9 / 1.1 / 1.1 | 3.2 |
| seconds per possession | 11.3 / 11.3 / 11.2 | 11.2 / 11.3 / 11.3 | 15.5 |
| within tolerance (of 18) | 8 / 7 / 6 | 9 / 11 / 10 | |
| weighted score | 4.0 / 6.2 / 6.7 | — | 0 |

The search also raised the foul rate ×1.28. That pushed FTA/FGA to .32–.37 and
caused foul trouble in full games, so `foul_scale` is held at 1 and the values were
retrained. The "after" column is that final configuration.

**What changed:**
- **Later, shallower help.** The help trigger went from 22 to 16.5 ft and tag
  range from 15 to 8.3 ft. The stunt stops 7.6 ft from the driver instead of
  4.5, and help leaves shooters more readily (penalty 8 → 3.6).
- **Slower reactions** (×1.3).
- **Heavier contests on jumpers.** The contest penalty is ×1.6, contests at
  release reach to 1.65 ft, and an open three is two points harder to make.
- Three times as many errant passes (the foul increase was reverted, see above).

The shot mix, eFG and contest tightness are now in range.

**Still off:** the search fixed the shot mix mostly by making threes *worse*,
not by making the rim *available*.
- 3P% is about on target (.34–.38), but only because open threes were made two
  points harder.
- Wide-open threes stayed at ~20% (target 44%). In this engine an open three
  is the best shot, so the learned policy takes every one it gets, and only
  contested or harder threes keep the share at 42%.
- Points per possession are still ~1.17 with an on-target eFG. The excess
  comes from too few turnovers (7–8%, target 13%).
- Turnovers resist tuning: tripling errant passes just made the offence pass
  less (0.9–1.1 per possession, assists .42–.53).
- Rim attempts now come ~75–80% from drives. Cuts, putbacks and post-ups have
  shrunk, and offensive rebounds are low (.20–.22).

**Bounds and mechanisms:**
- Only `contest_reach` ended near a bound (1.65 in 1.5–4.5).
- The mechanisms the search couldn't fix are structural: ball movement (the
  pass value), free-throw rate independent of rim volume, and the open-three
  premium.
- Suggested next changes:
  - Make passing reduce defensive organisation, for example rotations that
    compound after each swing, so passes create open threes instead of the
    defence always recovering.
  - Model fouls on drives separately from the shot.
  - Hold `foul_scale` at 1 in a follow-up search.

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
