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

## Choosing the match engine

Every league plays its games on one of three engines (`rules.match_engine`,
saved with the league):

- **statistical** (the command-line default): fast, a few milliseconds a game.
  It is calibrated to 17 NBA per-team-game stats. Nothing to watch.
- **spatial**: every game is a full spatial game, with ten players on a court, set
  plays, transition, fatigue and substitutions. It takes about 0.7 s a game, so a
  12-team, 82-game season takes about 6 minutes and a 30-team season about 15.
- **hybrid** (the browser's default): your team's games are spatial, so you can
  watch or coach any of them, and everyone else's are statistical. A 12-team
  season with playoffs takes about 2 minutes (statistical: ~13 s). League-wide
  numbers stay on the calibrated engine. Your team's games run a little faster
  (pace ~103 vs ~99) with fewer threes, so its stats are slightly off the rest of
  the league's until the spatial engine closes those gaps. Without a user team
  (`season`, `career`), hybrid plays everything statistically.

```bash
python -m hoopsim play --engine hybrid        # new game (or switch a loaded one): your games spatial
python -m hoopsim play --engine spatial       # ... or every game spatial
python -m hoopsim season --engine spatial --teams 8
python -m hoopsim game --engine spatial
```

The browser's new-game form has the same choice. Regular season, playoffs, the web
interface and `play` all go through `engine.play_game`, so standings, stats,
awards, morale, development and the offseason work the same on either engine.

Injuries and game-day form work as on the statistical engine, with its tuning:
- Injuries are rolled at dead balls from each player's court time since the last
  check, his injury proneness and how tired he is (about 0.17 a team-game on both
  engines). A hurt player leaves at that dead ball, like a foul-out, and can't
  come back; "X is hurt (sprained ankle)" shows in the play-by-play.
- Each player has a game-day form: a small random night (wider for inconsistent
  players) plus his morale (+/-1.8% at morale 80 / 20). It multiplies his make
  chances, the same fraction off every shot, like fatigue.

Differences on the spatial engine, for now (`spatial/season_game.py`):
- `game --pbp` has no text play-by-play. Watch the game instead
  (`python -m hoopsim watch --full`).

**Watching your games.** On the spatial or hybrid engine, your team's games record their
movement. In the browser, recorded games show a **watch** link next to **box**
(on Home → Recent results, and on League → Schedule). It opens the match viewer
for the whole game, with quarter buttons. The page fits the screen: the court on the left,
and tabs on the right for the play-by-play, box score, who's on the floor and (live) the
coach panel. A replay is about
0.8 MB compressed, so only your 10 most recent are kept in the save
(`engine.REPLAYS_KEPT`; save files are schema v4, and v1–v3 still load). Other
teams' games, and statistical-engine games, aren't recorded. Replaying them on
the spatial engine wouldn't reproduce the result in the standings.

**2D / 3D.** The viewer's **2D | 3D** switch (remembered between games) swaps the
flat court for a 3D one: a to-scale 94×50 ft floor with the same lines, backboards and
10 ft rims with nets, low-poly players in team colours with their numbers on the jersey,
and the ball at its real height with a shadow, so shots arc and lobs hang.
**Labels: Numbers | Names | Off** puts a team-coloured tag over each player (number, or
number and surname); the ball handler's tag has an orange rim and a ring at his feet.
While a shot is up a fading trail follows the ball, turning green (made) or red (missed)
as it reaches the rim, then clears. Cameras: **Broadcast** (the default: high on the
sideline, panning with the ball), **Baseline** (behind the basket at the end the play is
in; it changes ends with a quick dip once the ball is well over half court), **Follow**
(a low chase camera behind the ball that swings round on a change of possession) and
**Overhead**. Cameras glide between presets, and a possession that carries straight on
from the last keeps the camera moving instead of cutting. Drag to turn, scroll to zoom,
double-click to go back to the preset (the camera stays over the floor, out of the seats
and under the roof). Keys in 3D: **1–4** pick a camera, **C** steps through them, **L**
steps through the labels, **B** switches the players between Animated and Simple (see
*Animated players* below). Camera, labels and players are remembered. Add `?debug` to the page's
address to reach the 3D view from the console as `window.hoopsim3D`, with frame timing
and a seek helper. Without it, nothing is added to `window`. The court sits in an arena: team benches
and the scorer's table on the far sideline, photographers on the baselines, a lower bowl
and an upper deck with aisles and portals, LED boards and a centre-hung scoreboard with
the live score and clock, lights over a dark roof. Thousands of fans (mostly in home
colours) sway, stand up when the game is close and late, and jump when the home team
scores (bigger for threes and plays at the rim); away fans answer their team's big makes.
**Arena: Full | Light | Off** sets how much of it is drawn (Light: lower bowl only;
phones start there; remembered). It is all procedural (no images; under 30 KB more per page),
built only the first time 3D is shown, and the crowd is a handful of instanced meshes
animated on the GPU. It renders the same frame as
the 2D view, so playback, highlights, transitions and live coaching work the same.
Players move along a smooth curve through the ten-a-second positions, with a light
damping that never leaves them more than 0.6 ft from where the simulation has them, and
face where they run (or the ball when they stand). 2D stays the default: the 3D view is
only built when picked, and without WebGL the switch is disabled with a note. With
reduced motion on, the cameras hold still instead of following and the crowd sits still. It uses three.js r160
(MIT), vendored in `spatial/vendor/` and inlined once into every viewer page as inert
text that only runs when 3D is switched on (about 650 KB per page; pass `three=False`
to `export.build_html` for a 2D-only page), so replays and live games work offline.

**Animated players** (3D). In the browser interface the 3D view's players are one rigged
low-poly model (about 2,500 triangles, 23 joints), cloned per player with three.js's
`SkeletonUtils`, tinted in team colours with the number on the jersey, and driven by
motion-capture clips through an `AnimationMixer`. **Players: Animated | Simple** (key **B**,
remembered) switches back to the simple bodies. The action cues below pick the clips:
jump shot (gather, then release on the frame the ball leaves), free throw, layup, floater,
hook, dunk and alley-oop (he carries the ball to the rim), chest and overhead pass, catch,
contest (hand up), block, steal reach, rebound jump, screen, charge fall. Everything else is
read off the frames: idle and defensive stance, walk/jog/sprint, dribbling standing and on
the move (the ball bounces under the hand), defensive slides and backpedalling while facing
the ball, and a planted foot on a hard stop or cut. Clips cross-fade; a shooter turns to
the rim, a passer to his receiver, a defender to his man. Against foot sliding, run and
slide cycles play at the player's real speed (stride length over ground speed, blended
between the walk, jog and sprint cycles in step), and a player standing still stays put
until the simulation has moved him half a foot. Jumps are lifted so the hands meet the
ball (a rebound, a block, the rim on a dunk). A clip that is still running when a
possession ends plays on through the pause and into the next one. Replays recorded before
the cues existed get shots, passes, catches and rebounds read off the frames instead.

The files (`spatial/assets/player.glb`, 0.45 MB, plus three.js r160's `GLTFLoader`,
`BufferGeometryUtils` and `SkeletonUtils` in `spatial/vendor/`, 0.15 MB, unmodified) are
served by the web app at `/viewer-assets/` and fetched only the first time 3D is shown;
nothing is added to the page itself. If they fail to load, the view says so and keeps the
simple bodies; if the device can't hold about 18 frames a second with them, it switches to
Simple by itself (unless you picked Animated by hand). Exported pages
(`python -m hoopsim watch`) keep the simple bodies and stay self-contained; add
`--animated` (or `export.build_html(..., animation="embed")`) to carry the players inside
the page, about 0.8 MB more. The motion is from the CMU Graphics Lab Motion Capture
Database (free for any use); the model is generated by `tools/build_player_assets.py`,
which also cuts and retargets the clips. Sources, licences and the clip-by-clip list are in
`spatial/assets/LICENSES.md`. Measured cost: about 0.6 ms of CPU per frame for ten animated
players (0.1 ms for the simple bodies); see `window.hoopsim3D.stats()` and `.bodies()` with
`?debug`.

**Animation cues** (`spatial/sim.py` `ACTIONS`). Each recorded possession carries an
`"actions"` list next to its frames and events: `[frame, player, action, other]`, meaning
player `player` (0-9, as in the frame's positions; `other` likewise, or -1) starts doing
`action` at that frame. The engine records what it decides: `pass`, `lob_pass`, `inbound`,
`catch`, `drive`, `cut`, `screen`, `post_up`, `jumper` (the gather) and `jumper_release`,
`layup`, `dunk`, `floater`, `hook`, `fadeaway`, `alley_oop`, `heave`, `free_throw`,
`contest`, `block`, `steal`, `intercept`, `take_charge`, `charge`, `foul`, `rebound` and
`rebound_jump`. Dribbling, running and defensive stances follow from the frames (who holds
the ball, how fast each player moves). They are for a 3D view to animate bodies with: they
add about 1-2% to a saved replay, cost nothing when no replay is recorded, and change
nothing in the play (the legacy fingerprint test hashes the play without them). Older
replays simply have none.

**Highlights** (`spatial/highlights.py`). The viewer has Football Manager-style
highlight modes for full games: **Key**, **Extended** and **Full**. Your choice is
remembered between games.

- **Key** (about 15–60 of ~210 possessions, more in close games): lead changes and
  ties, the basket that completes an 8-0 run, alley-oops, and-ones, buzzer-beaters,
  plays out of a timeout, and everything in a close finish (the last five minutes of
  the 4th quarter or overtime, within eight).
- **Extended** (about 85–110): key moments plus made threes, fast-break baskets,
  baskets that take the lead, steals, blocks, charges and putbacks.
- **Full**: every possession.

Skipped possessions go by in a line ("⏩ 3 possessions: BWB +5, BWS +2"), so the
scoreboard, clock and play-by-play stay right. **Full for close finishes** (on by
default) shows every possession of the last two minutes of a 4th quarter or
overtime within five. Next, Prev, the arrow keys and the quarter buttons step
through what the mode shows; the scrub bar still reaches any possession. In a live
game the simulation still runs every possession, so your timeouts, subs and shouts
work as before. In Key or Extended mode it runs further ahead of what you see (up
to about eight possessions), so a command can land a little later. Levels come
from what each possession recorded (score, events, game clock), so they never
change a game, and older replays get them when opened.

**Watching live.** On the spatial or hybrid engine, the top bar has a **▶ Watch**
button, in the regular season and in the playoffs. It plays on to your next
game, including the rest of that day's games, then opens the game full screen
over the app, so the record, inbox and standings behind it can't spoil the
result. The viewer starts playing at once, the play-by-play shows only what has
happened so far, and the box score appears at the final buzzer. **Back to the
office** returns to the app. If an AI team makes you a trade offer on the way,
it stops there, as the normal sim does. A watched game is simulated in full
before you see it; to make decisions, coach it live instead.

**Coaching live** (`spatial/live.py`). **🏀 Play live** plays on to your next game
(regular season or playoffs) and puts you on the bench. The game is simulated one
possession at a time, only a possession or two ahead of what you're watching, so
your decisions change what happens next. The **Coach** panel has:

- **Call timeout.** Taken at the next dead ball, or as soon as your team has the
  ball. It uses one of your timeouts, gives everyone a breather and stops the
  other team's run.
- **Substitutions.** Click a player on the floor, then one on the bench. He checks
  in at the next dead ball. The panel shows each player's energy, fouls and
  minutes. Points stay out of the panel until the final buzzer, because the
  simulation runs a possession or two ahead. An injured player is shown as out
  ("injured") and can't be picked; a queued sub involving him is dropped.
- **Auto subs.** On, your bench coach keeps running the rotation. Off, only
  forced changes (foul-outs and injuries) are made for you.
- **Play out of a timeout.** Pick a set before you call the timeout (or while it's
  pending): swing, hand-off, down screen or pick-and-roll, with an optional
  target (the screener in the pick-and-roll, the shooter coming off the down
  screen). Your team runs it on its next half-court possession, end of game or
  not, if at least 10 seconds are left on the shot clock; the play-by-play says
  "Out of the timeout". A timeout taken on a live ball (after a rebound or steal)
  now means an inbound, not a fast break.
- **Shouts.** Touchline instructions that tilt your players' close calls: a small
  bonus or penalty on the learned option values (`HalfCourtGame.shout_bias`), not
  new values. Offence: *Attack rim*, *Threes*, *Feed post*, *Slow down* (use the
  shot clock, walk it up), *Push pace* (early shots, run after rebounds and
  makes) and *Get the ball to* a player. Defence: *Pressure* (closer on the ball:
  more strips, a few more reach-ins), *Protect paint* (sag in: fewer layups, more
  threes against) and *Foul now* (foul at once whenever you trail in the 4th or
  overtime, through the same intentional-foul path as the bench coach). One shout
  per group (rim/threes/post, slow/push, pressure/paint); a new one replaces the
  old. A shout lasts until you cancel it, or for your next 3, 5 or 10 possessions at
  that end (*Foul now* counts the fouls). Active shouts show as tags with a ×.
  *Feed post* also lines the team up with a post-capable big on the block (as the
  inside-focus tactic does). Otherwise, with stretch bigs spacing five-out, there's
  often nobody to feed. Over 16 games each, against the same games without it
  (after the three-point changes below): *Attack rim* took the rim share of shots
  from 45% to 50% (threes 32% → 26%), *Threes* the three share from 32% to 38%,
  *Feed post* post-ups from 82 to 221, and *Pressure* strips from 0.028 to 0.054
  per possession (cushion 5.6 → 4.9 ft). Before those changes, over three games:
  *Slow down* raised seconds per possession from 14.2 to 16.1 and *Push pace*
  lowered it to 12.0, and *Protect paint* raised the opponent's three share from
  45% to 56%. The tests use 8 games per shout, because 3 were too noisy.
- **End of game.** Overrule the bench coach's late-game calls for your team: hold
  for the last shot, go two-for-one, and foul to stop the clock (when trailing in
  the last 45 seconds), each *Bench coach* (the default), *On* or *Off*.
- **Match tactics.** Pace, three-point rate, crashing the glass, offensive focus and
  defensive scheme, for this game only. Your saved tactics come back afterwards.
- **Sim to end.** Your bench coach finishes the game.

Commands are JSON posted to `/api/live/command`: `timeout` (optional `play`,
`target`), `ato`, `sub`, `cancel_subs`, `auto_subs`, `tactics`, `shout` (`shout`,
optional `possessions`, `player`), `cancel_shout` and `endgame` (`last_shot`,
`two_for_one`, `foul`: `auto`, `on` or `off`). Bad ones get a 400 with the reason.

Timeouts, substitutions and injuries from both benches show in the play-by-play. While a
game is live, the rest of the app waits: no sims, deals or saves. **Back to the
office** in the middle of a game asks first, then your bench coach finishes it.
The result, injuries, news and replay are recorded like any other game, then the
rest of that day is played. Under the hood, `FullGame.iter_game()` pauses after
every possession (`run()` plays straight through, with identical results), and
`attach_manager()` queues the coach's commands for the next break. The API routes
are `/api/live/start`, `/next`, `/command`, `/finish` and `/page`.
- The full-game numbers (pace ~103, ~116 points, three share ~.36) are in the
  "Full games" section below.

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
| **Squad** | sortable roster tables (overview, ratings, stats, contracts) for any team, finances. Click any name for the player card: attributes, scouted potential with its range, scouting notes, ability by position, player instructions, this season's line and a career section: small charts of points, rebounds and assists per game and current ability by season (ability from end-of-season snapshots, plus today's), and a season-by-season table for the regular season and the playoffs. |
| **Tactics** | starting five by position (with each player's ability at that slot), minutes that must total 240 (with a "balance" helper), the assistant-coach toggle, team tactics and player instructions. |
| **Transfers** | free agents with their asks and a contract form that shows how you'd pay (cap space, mid-level exception, minimum); a trade builder with live salary-matching and roster feedback and the other team's verdict. |
| **League** | standings, leaders, schedule and results with box scores (current season), a daily scoreboard, the playoff bracket and past seasons. |
| **Calendar** | The season a month at a time: your games (home/away, results, **B2B** badges), league games per night, opening night, Thanksgiving, Christmas, the All-Star break, the trade deadline and the playoffs. Click a day for every game on it, and **Sim to this day** to play up to it. See *Schedule* below. |
| **Stats** | *League leaders*: per-game points, rebounds, assists, steals and blocks, FG%, 3P% and FT%, and advanced lists (true shooting %, usage %, share of team FGA, points/rebounds/assists per 36), filtered by season, regular season or playoffs, and position; your players are highlighted. To qualify a player needs 60% of the most games any team has played (4 games in the playoffs), plus 5 FGA, 2 3PA or 2 FTA per game for the shooting lists and 15 minutes per game for the rate lists. *Team stats*: a sortable table of points for and against, pace, offensive, defensive and net rating and the four factors for offence and defence (eFG%, TOV%, ORB%/DRB%, FT rate, 3PA rate). *Game log*: every game a team has played this season with the score, running record and top scorer, linked to the box score and, for recorded games, the viewer. |
| **Offseason** | one screen per step: team options, re-signing (asks and the chance he stays), the draft board pick by pick, every free-agency round, trade offers and roster cuts. Any decision, a whole step or the rest of the summer can be handed to your assistant. |

The offseason runs as resumable steps (`start_offseason`, `offseason_pending`,
`answer_offseason` in `season.py`); `run_offseason` drives the same steps with a
`Manager`, so the terminal game and the AI behave exactly as before. The API is
under `/api` (interactive docs at `/api/docs`).

The stats pages are read-only (`/api/stats/leaders`, `/api/stats/teams`,
`/api/players/{id}/history`, `/api/team/gamelog`) and computed from box scores in
`hoopsim/stats_tables.py`, cached until the next game is played. Possessions per
team-game are estimated as FGA + 0.44 FTA - ORB + TOV and averaged over the two
teams, so a game's teams share one count; ratings are points per 100 of those
possessions and pace is possessions per 48 minutes. Box scores are only kept for
the season being played, so past seasons' leaders come from the players' career
lines (without the usage numbers, which need team totals) and team stats cover the
current season only.

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
pause/speed/skip controls, in 2D or 3D (see *2D / 3D* above).

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
python -m hoopsim watch --full                   # a whole spatial game in the viewer (~4 MB page)
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
  baskets, the players run the floor at ten updates a second (the viewer shows the
  whole court sideways, as on TV: each team attacks one basket and they switch at halftime,
  so possessions flow into each other; a quarter break or a highlights skip fades through). A team pushes depending on how it got the ball, its pace
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
  bench in blowouts. Fouled-out players leave at once, and hurt players at the
  dead ball where it happens (injuries are rolled as in the season engine).
- **End of game.** Trailing teams foul to stop the clock and call a late timeout to
  advance the ball. Leading teams milk the shot clock and dribble out the end. Teams
  hold for the last shot of a quarter, go two-for-one, and look for a three when
  down three. These are passed to the half-court decisions as small value biases.

Measured over 60 games (seed 0; per team-game), with everything above plus
inbounds after makes and dead balls, against 2025-26 Basketball-Reference league
averages (transition and minutes are estimates; sources in `measure.py`):

| | Sim | NBA |
|---|---|---|
| Pace | 101.9 (135 before set plays) | 99.4 |
| Points per team | 111 (109 per 100) | 115.6 (115.8 per 100) |
| Transition share of possessions | 14.7% | ~15% (est.) |
| Transition points per possession | 1.25 | ~1.26 (est.) |
| Turnovers per 100 possessions | 16.1 | 14.6 |
| Three share / eFG | .341 / .536 | .415 / .546 |

**Inbounds after makes and dead balls.** After every made basket or made free
throw, the court view shows the inbound: a big takes it out under the basket and
the guard comes back for it. Then comes the walk-up (`inbound_walkups`; before, a
possession the team didn't push jumped straight to half-court positions). The
walk-up is real movement, so it sets the game clock. Walk-ups go at 55% of top
speed (was 80%), and retrieving the ball and inbounding takes 1.5–2.8 s (was
1.0–1.8). A team then reaches the frontcourt with about 18.6 on the shot clock
after a make and 16.6 after a defensive rebound.

After a dead ball (a non-shooting foul before the bonus, a violation, a ball out
of bounds, a turnover that stops play), the next possession starts with a
throw-in from where play stopped (`dead_ball_inbounds`, `DeadBallInbound` in
`transition.py`). Nobody is teleported. The ten players start where they were,
and the court turns around if the ball changed hands. The nearest wing or forward
takes it out on the sideline level with the ball, no nearer the baseline than the
free-throw line extended. A ball that went out under the basket comes in from the
baseline. While he walks to the spot, his teammates go to their spots and the set
defence denies them, each defender on his own man. He throws it to the most open
man, and a defender in the passing lane can pick it off (about 0.4% of
throw-ins; it's a turnover and a live ball). The game clock is stopped until the
ball is touched, so the setup (about 3.8 s) is shown but uses no game time. The
shot clock starts on the touch at 24, or at 14 or more after a foul. In the
frontcourt the half-court offence starts on the catch. In the backcourt the
receiver walks it up first. Out of a late timeout ("advance the ball"), the
throw-in comes from the frontcourt sideline at the 28-ft mark. Over 240 games,
pace is 101.8 (was 101.5), points per 100 109.7 (110.5) and turnovers per 100
15.9 (15.9); 60-game samples move by about a point either way on chance alone.
The scoring leader averages ~28 points, and full games take about 0.9 s each.

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

### Rotation lag (`scramble`)

The defence now carries a *scramble* level. A pass to a man whose defender is
out of position (helping, stunting, or more than `scramble_bend` = 4 ft beyond
his closeout spot) raises it by one, up to 3. A pass against a set defence
lowers it by one, and it recovers by itself at `scramble_decay` = 0.2 levels a
second. While scrambled, the receiver's defender reads the pass later (his
reaction × (pass_read + `rotation_lag` × scramble), with `rotation_lag` = 0.6),
and the others hold their old spots a beat longer (ball-watching). Passers see
the lag when they judge how open a teammate will be, and pass options carry the
scramble level as a feature for the learned values. `LEGACY_PARAMS` turns it off.

Recovery speed matters most. At 0.6 levels a second the scramble faded before
the next pass (a touch lasts ~2.5 s), so it never compounded. Quick retrains on
the training leagues (score / metrics within tolerance):
no lag 1.35 / 9; lag 0.6 recovering 0.6/s 1.47 / 11; lag 0.6 recovering 0.2/s
0.90 / 14; lag 1.0 recovering 0.2/s 2.38 / 11 (too many open threes, .50).

Held-out leagues 0 / 1 / 2 after a full retrain, against the calibrated engine
without lag:

| | without lag | with lag | target |
|---|---|---|---|
| points per possession | 1.17 / 1.19 / 1.16 | 1.13 / 1.17 / 1.13 | 1.10 ± .04 |
| eFG | .515 / .536 / .521 | .495 / .529 / .525 | .527 |
| three share | .46 / .42 / .40 | .45 / .46 / .45 | .42 |
| 3P% | .360 / .380 / .342 | .324 / .365 / .354 | .362 |
| wide-open threes | .21 / .26 / .24 | .31 / .44 / .35 | .44 |
| passes per possession | 0.9 / 1.1 / 1.1 | 1.0 / 1.1 / 1.2 | 3.2 |
| seconds per possession | 11.2 / 11.3 / 11.3 | 11.2 / 11.3 / 11.4 | 15.5 |
| within tolerance (of 18) | 9 / 11 / 10 | 8 / 11 / 13 | |

Lag fixes most of the wide-open-three gap and trims points per possession, but it
does **not** lengthen possessions or add passes. The learned weight on the
scramble feature stays near zero: the offence takes the open look the first skip
pass creates rather than passing again. Possessions have ~4.5 s of action after
the bring-up at ~2.5 s per touch (realistic), so what's missing is the number of
touches: NBA offences run entries and actions (reversals, hand-offs, off-ball
screens) before attacking, which the next section adds.

### Set plays (`plays.py`)

After the bring-up, the offence runs entry actions before the learned decisions
take over:
- **swing:** a reversal pass to the next perimeter man;
- **handoff:** the handler dribbles at a wing and hands it off; the wing's defender
  is caught on the handler's body for a moment;
- **downscreen:** a big screens down for the corner man, who pops to the wing
  while the wing replaces him in the corner.

Each entry pass is a real pass: it can be picked off and it feeds the rotation lag.
At each catch the receiver reads the defence. Wide open (a catch worth
`entry_read` = 1.12 points), he shoots; his man caught on the screen or well
behind, he drives; otherwise the play goes on. Patience comes from the pace
tactic: `entry_actions` = 3 at pace 10, fewer for fast teams and more for slow
ones. No new action starts with under 10 s on the shot clock, and entries are
skipped in end-of-game situations and in flows out of transition.
`LEGACY_PARAMS` turns them off.

Quick retrains on the training leagues (score / within tolerance): no entries
1.41 / 11; 2 actions 1.11 / 11; 3 actions 0.87 / 14; 3 actions with the 10 s
cutoff 0.81 / 14.

Held-out leagues 0 / 1 / 2 after a full retrain:

| | lag only | lag + set plays | target |
|---|---|---|---|
| points per possession | 1.13 / 1.17 / 1.13 | 1.08 / 1.06 / 1.08 | 1.10 ± .04 |
| eFG | .495 / .529 / .525 | .527 / .508 / .549 | .527 |
| three share | .45 / .46 / .45 | .37 / .33 / .38 | .42 |
| 3P% | .324 / .365 / .354 | .349 / .355 / .396 | .362 |
| wide-open threes | .31 / .44 / .35 | .49 / .59 / .59 | .44 |
| turnovers | .079 / .083 / .090 | .156 / .159 / .159 | .13 |
| assists per make | .40 / .57 / .56 | .67 / .68 / .72 | .60 |
| passes per possession | 1.0 / 1.1 / 1.2 | 1.8 / 1.8 / 1.8 | 3.2 |
| seconds per possession | 11.2 / 11.3 / 11.4 | 14.2 / 14.2 / 13.9 | 15.5 |
| within tolerance (of 18) | 8 / 11 / 13 | 12 / 12 / 9 | |

Set plays bring possession length, passes and assists close to the NBA and
points per possession onto the target. They overshoot on turnovers (the
calibrated bad-pass rate was set when teams barely passed) and wide-open threes,
and the three share is now a little low. Those are interactions for the next
calibration run: `rotation_lag`, `scramble_decay`, `entry_actions` and
`entry_read` are now in `SEARCH_SPACE`.

**Second calibration run (no change adopted).** A refinement from these
defaults (4 noise runs, then the evolution strategy with sigma 0.10, 25
generations, 39 min) improved the training-league score from 0.87 ± 0.14 to
0.49 with small nudges: help leaves shooters more readily, slightly deeper
stunts, slightly tighter closeouts and fewer dribble turnovers. `entry_actions`
ended at its upper bound (3). After a full retrain the held-out score was 1.43
against 1.46 for the current defaults, which is noise, so the defaults were kept.
The remaining gaps (turnovers ~16%, wide-open threes ~50–58%, ~1.8 passes and
~14 s per possession) don't respond to these settings. They are structural:
entry passes are lost as often as any other pass, and the search wants more
patience than three entry actions allow.

### Safe routine passes and second actions

**Routine passes.** A pass between perimeter players (both 19+ ft from the rim),
25 ft or shorter, from a passer under no pressure to a man who isn't being
denied, is routine. Its steal and errant-pass chances are scaled by
`routine_pass_risk` = 0.4, and passers see the lower risk. Before this, 3.8% of
passes were lost (NBA ~2%), so set plays pushed turnovers to ~16%.

**Second actions.** "Reset" is now an option for the learned decisions: with 11+ s
on the shot clock, the handler on the perimeter and no action running, he can
run another entry action instead of attacking (up to `second_actions` = 2 per
possession; slow-pace teams lean toward it). Stalled attacks (a drive that dies,
a screen that never comes, a post-up with nothing) also reset when the clock
allows. In practice resets are rare (~3% of possessions): after three entry
actions there is seldom 11 s left, and attacks rarely stall in this engine.

Held-out leagues 0 / 1 / 2 after a full retrain:

| | set plays | + safe passes + resets | target |
|---|---|---|---|
| points per possession | 1.08 / 1.06 / 1.08 | 1.10 / 1.13 / 1.13 | 1.10 ± .04 |
| eFG | .527 / .508 / .549 | .516 / .538 / .552 | .527 |
| three share | .37 / .33 / .38 | .40 / .36 / .41 | .42 |
| 3P% | .349 / .355 / .396 | .336 / .372 / .391 | .362 |
| wide-open threes | .49 / .59 / .59 | .48 / .56 / .55 | .44 |
| turnovers | .156 / .159 / .159 | .124 / .127 / .122 | .13 |
| passes per possession | 1.8 / 1.8 / 1.8 | 2.0 / 1.9 / 1.9 | 3.2 |
| seconds per possession | 14.2 / 14.2 / 13.9 | 14.6 / 14.5 / 14.2 | 15.5 |
| within tolerance (of 18) | 12 / 12 / 9 | 14 / 13 / 10 | |
| weighted score | 1.05 / 1.81 / 1.51 | 1.15 / 1.73 / 1.62 | 0 |

Turnovers are fixed and more metrics are in range. The weighted score is
unchanged within noise, because points per possession rose a little once fewer
possessions ended in turnovers. In full games pace fell from 111 to 107.5 and
turnovers per 100 match the NBA (14.8 vs 14.6).

### Pace: closing pick-and-rolls and inbound time

Game-clock time per possession was 13.4 s (NBA: 2,880 s / 2 / 99.4 = 14.5 s): about
3.1 s of bring-up, 2.2 s in the open court and 8.1 s of half-court play. Bring-up
times were realistic, so the shortfall is in the half court. Half-court shots came
almost entirely with 15–7 s on the shot clock (92%; the NBA has a quarter of its
shots under 7 s), and possessions averaged only 1.7 entry actions because early
reads and the 10 s cutoff ended sets before their last action.

Changes:
- **Closing pick-and-roll.** A set's last action, or any action once the clock is
  under 14 s, can be a pick-and-roll (`entry_pnr` weight 0.5) that then plays out
  with the learned reads. Pick-and-rolls now feature in ~24% of possessions (was ~1%
  chosen freely).
- **Inbound after a make** runs 2–3.5 s of game clock (was 1–2.5), as an NBA
  inbound does.
- `entry_drive_gap`: a freed catch from an entry is driven only with 5 ft of
  daylight (hand-offs and down screens generally do create it).
- Up to 4 entry actions for slow teams. Full games take `params`.

Things that did *not* move pace: 4 entry actions at pace 10, and starting actions
down to 8 s on the clock. After a set, the learned offence attacks within a second
or two, and first shots still come at ~11 s.

Full games (60): pace 107.4 → 103.9, points per 100 108 → 112.6 (NBA 115.8), with
fouls, free throws and turnovers still on target (8 of 10 in range). The half-court
yardstick went the other way: 1.16–1.17 points per possession on the held-out
leagues (target 1.10 ± .04; score 1.50 → 1.66), because closing pick-and-rolls are
productive. The two yardsticks now disagree: the same engine is above the half-court
estimate yet below the real per-100 rate in full games, where fatigue and benches
play. The full-game numbers come from Basketball-Reference and the half-court ones
are estimates, so the full-game numbers decide.

### Realism audit (`python -m hoopsim.spatial.audit`)
The audit plays recorded full games and checks what a 3D view would show against NBA
player-tracking figures:
* bodies: speed, distance, acceleration, teleports, overlaps, players off the floor;
* floor: spacing and defenders between their man and the rim;
* ball: pass speed and shot arc;
* shots: zones, corner threes, distance, closest defender, shot clock, passes per
  possession.

Each line reads ok, watch or off. The first run found players teleporting about 275
times a game:
* **Free throws:** all ten snapped onto the lane after every shooting foul.
* **Offensive rebounds:** the rebounder snapped up to 9 ft onto the ball.

Now everyone walks or jogs to the lane in dead-ball time, and the rebounder runs to the
loose ball. Teleports fell to about 0.1 a game, with box-score calibration unchanged.

### Patient offence, human movement and spacing
The next audit flagged hurried offences (1.6 passes per half-court possession, 7.6% of
shots with 7 or fewer seconds on the shot clock), snappy movement (99th-percentile
acceleration 28 ft/s²) and bunched attackers. What the measurements showed:
* **Every set possession ended mid-clock.** 79% of shots came with 18-7 s left. The
  handler mostly picked the option the learned values rated best (72% of shots), so
  temperature wasn't the cause. With all options valued within ~0.05 points, the first
  ordinary look after the entry (a drive, a so-so jumper, an entry read) was taken. Set
  entries ended in a drive 0.3 times per possession, and nothing kept the ball moving.
* **Passing was too risky to do more of.** About 5% of passes were lost (NBA ~2-2.5%),
  and the values had learned that.
* **Time went to the backcourt.** Sets started with ~17.8 s on the shot clock (walk-up
  at 55% speed, 3.5-6 s bring-up, 1.5-3 s before the first action).
* **Movement:** the acceleration cap itself (22 + 1.2 per speed point) set the 99th
  percentile. Free-throw walks started and stopped instantly, and screens stopped a
  defender dead in a tick.
* **Spacing:** a receiver took the spot where he caught the ball, so spots drifted
  together over a possession. The audit also counted free-throw lineups as half-court
  spacing (that alone was 50.7% → 45.6%).

Changes:
* **Patience** (`SpatialParams.patience*`, `HalfCourtGame.patience_bias`). Against a set
  defence, shots, drives without an edge and post-ups lose up to 0.3 points of value
  with 14+ s on the shot clock, fading to nothing at 4 s. Holding the ball loses 30% of
  that, so it gets swung instead. Great looks (worth 1.25+ points) and drives past a
  beaten man are exempt, and threes pay only a quarter. Flows out of transition and
  end-of-game situations are exempt too. Slow teams are more patient. The "look for
  threes" shout goes from 0.06 to 0.09 to keep its effect.
* **Sets:** entries can start down to 8.5 s (was 10) and run to 6 s (was 7.5). A freed
  catch is driven only with 7 ft of daylight (was 5). The first action comes sooner
  (0.8-2 s after the bring-up, swing passes 0.4-1 s).
* **Safer passing:** `pass_steal_scale` 0.213 → 0.045, `errant_pass` 0.018 → 0.007,
  `dribble_turnover_per_sec` 0.058 → 0.045. A perimeter pass is still routine with the
  on-ball man 2-3 ft away.
* **Backcourt:** walk-ups at 70% speed (was 55%), bring-up 3-4.5 s (was 3.5-6), and a
  little more pushing (after defensive rebounds 0.38 → 0.43, after makes 0.06 → 0.08).
* **Movement:** acceleration is 15 + 0.6 per speed point (`ACCEL_BASE`,
  `ACCEL_PER_POINT`). `Agent.steer` is shared by half-court moves, throw-in walks and
  free-throw walks, and screens brake at 1.4× that. Slower closeouts opened shots, so
  jump shots take longer to get off (catch 0.30 → 0.45 s, off the dribble 0.36 → 0.50 s),
  and rim finishing is 3 points harder (`rim_make_adj`).
* **Spacing:** after every catch, a perimeter man within 12 ft of the ball or a
  teammate's spot moves to the nearest open spot (top, slots, wings, corners;
  `respace`). Whoever brings the ball up takes the point.
* `defensive_rebound_edge` 4.58 → 3.2 (ORB% was .21). The values were retrained.

Audit, 24 games (seed 0):

| | before | after | NBA |
|---|---|---|---|
| passes per half-court possession | 1.58 | 2.64 | 2.5-3.6 |
| shots with 7-0 s on the shot clock | 7.6% | 16.0% | 16-26% |
| shots with 24-18 s | 12.9% | 12.0% | 14-24% |
| acceleration, 99th percentile | 28.1 ft/s² | 20.0 | 8-22 |
| top speed / average speed | 21.4 / 4.56 mph | 21.7 / 4.79 | ≤21.5 / 3.9-4.8 |
| two attackers within 6 ft (live half court) | 50.7% (45.6% w/o free throws) | 38.7% | 15-45% |
| closest defender 0-2 / 2-4 / 4-6 / 6+ ft | 13.8 / 29.6 / 24.8 / 31.7% | 10.6 / 23.1 / 19.8 / 46.6% | 5-11 / 28-38 / 28-36 / 22-32% |
| rim / threes / corner share of threes | 34.5 / 37.8 / 21.9% | 37.6 / 42.4 / 33.1% | 28-36 / 38-44 / 20-28% |
| ok / watch / off | 14 / 8 / 4 | 14 / 8 / 4 | |

Full games, 64 per league (`measure --games 64 --jobs 2`, seeds 0 / 1 / 2):

| | before | after | NBA 2025-26 |
|---|---|---|---|
| pace | 103.8 / 104.5 / 106.1 | 96.7 / 98.3 / 99.9 | 99.4 |
| points | 114.3 / 115.1 / 114.7 | 114.7 / 119.1 / 118.5 | 115.6 |
| points per 100 | 110.1 / 109.7 / 107.7 | 118.3 / 120.4 / 118.4 | 115.8 |
| turnovers per 100 | 16.7 / 15.5 / 16.3 | 14.3 / 13.3 / 13.8 | 14.6 |
| three share | .371 / .396 / .395 | .406 / .425 / .421 | .415 |
| eFG | .568 / .542 / .559 | .584 / .581 / .590 | .546 |
| ORB% | .207 / .236 / .187 | .254 / .270 / .229 | .260 |
| within tolerance (of 13) | 6 / 10 / 6 | 12 / 11 / 10 | |

Full games take about 0.95 s each (was 0.87).

Still off:
* **Wide-open shots and eFG.** Patience means early shots are mostly great looks, and
  closeouts are slower with human acceleration. So 6+ ft shots rose to ~46% and eFG to
  ~.585. A longer release, earlier help, less sag and harder rim finishing each helped
  only a little. Pulling eFG down to .546 would take ~7 points a game, unless
  possessions also produce more shots (FTA and ORB are now on target). A defence that
  recovers better on the second and third pass is the next job.
* **Early shots** (24-18 s) stay ~11-12%. They come almost only from transition, and
  half-court sets start with ~19.5 s left.
* **Corner threes** are a third of all threes (respacing fills the corners).
* Retraining the values moves full-game numbers a lot (2-3 possessions of pace between
  retrains with near-identical settings), and 32-game samples differ by up to 7 points
  a game from seed to seed. Judge changes on 64+ games.

### Defence that recovers
After the patient offence, 46.6% of shots had no defender within 6 ft (NBA 22-32%) and
eFG was ~.585. Logging the closest defender at every shot, where the shooter's man was
a second earlier, and the defender's position, target and speed from the pass to the
release showed:
* **Mostly catch-and-shoot threes, after one or two passes.** 74% of them were wide open.
  At the throw, the receiver's man was ~14 ft from the catch point (his help spot plus
  ~5 ft of trailing a man who was relocating), and he took ~0.6 s to read the pass
  (`pass_read` plus scramble lag).
* **Closeouts overran and then backed off.** Defenders had no braking: they sprinted
  at their spot, overshot it and came back. When the shooter rose, a closing defender
  was retargeted to only 2.5 ft closer than where he was, so he eased off. On the
  catch he switched to on-ball spacing (5-7 ft off) mid-closeout.
* **Off-ball defenders chased stale targets.** Their spot was recomputed only at
  each read (~0.4 s), from where the man was, not where he was going.
* **Shooters waited for wide-open looks.** The handler predicted a closing defender
  would arrive right on top of him, so he passed up every catch with a closeout
  coming. Even with no patience at all, half of all threes were wide open.
* **Respacing wasn't the main driver.** Turning `respace` off moved wide-open shots by
  ~2 points, and keeping it away from the corners moved corner threes by 2-3 points.
* **Rotation tweaks did little.** A faster `pass_read`, less scramble lag, X-out
  rotations (the nearest defender takes a skip pass), sticky helpers and helpers
  meeting the driver nearer the rim each moved the closest-defender mix by 1-2 points
  or less. Most of these were dropped.

Changes (all in `SpatialParams`, off in `LEGACY_PARAMS`):
* **Braking** (`defender_brake` 0.8): a defender slows in time to stop at his spot
  (decelerating at 80% of his acceleration limit). Closeouts arrive under control
  instead of overrunning. This was the biggest single change (6+ ft from ~36% to ~29%
  on its own).
* **Closeouts carry through the shot:** `closeout_burst` 1.35 (a straight-line sprint
  accelerates harder; the audit's acceleration 99th percentile stays 20.0 ft/s²).
  `contest_run` 5 ft and `contest_stop` 3 ft: a defender still more than 5 ft away
  when the shooter rises keeps sprinting and stops a stride off him. One already
  close steps in at most `contest_lunge` = 1 ft (was 2.5) and no closer than 3 ft,
  unless he was already that tight. The shot decision predicts the contest the same
  way (`predicted_contest`): a closeout stops a stride off, it doesn't run through the
  shooter.
* **Off-ball defenders track their man** (`offball_track`, `offball_lead` 0.5 s).
  Between reads they move with their man and play where he is going. The ball's
  position and the sag are only taken in at each read, so ball-watching and rotation
  lag still work. Tracking runs every other tick (5 Hz).
* **Sag set by shooting** (`sag_shooter_slope` 0.06): sag shrinks 6% per point of three
  rating above 11 (within 0.4-1.4×). Defenders stay attached to shooters and sink off
  poor ones.
* **Pull-ups** (`pullup_lead` 0.45 s, was 0.2): the on-ball man gives ground on a pull-up
  as on a drive. Before, he stood in the dribbler's path, and 52% of pull-up jumpers had
  a defender inside 2 ft.
* **Body separation** gets a second pass in the same tick (a body pushed into a third).
  This is physics, so the legacy pin was re-pinned.
* **Corners:** respacing treats a corner as 20 ft further away (`respace_corner_cost`).
  The corner man lifts to the wing on a drive on his side (`corner_lift`). In early
  offence the ball-side wing stops at the wing 70% of the time (`ball_side_wing`).
* **Rebalancing for the stronger defence:** `closeout_base` 4.82 → 5.5 and
  `closeout_min` 2.8 → 3.4 (closeouts now stop where they aim). `contest_outside` 1.602 →
  1.7. `rim_make_adj` -0.03 → -0.025. `patience_three` 0.25 → 0.8: with tighter
  closeouts the offence otherwise went to 50%+ threes. Pace and turnovers changed too:
  `entry_actions` 3 → 2, `patience_full` 14 → 16, `patience_none` 4 → 3,
  `pass_steal_scale` 0.045 → 0.037, `errant_pass` 0.007 → 0.0062,
  `dribble_turnover_per_sec` 0.045 → 0.04, walk-up `jog` 0.70 → 0.74, `push_after_drb`
  0.43 → 0.39.
* The values were retrained (4 rounds, resumed from the previous values). Retrains
  were again noisy: with near-identical settings the full-game three share came out
  anywhere from .39 to .54. The pass-steal, pull-up, patience-window, corner and
  push changes came after the last retrain and were checked on all three seeds
  without retraining.

Audit, 48 games (seed 7):

| | before | after | NBA |
|---|---|---|---|
| closest defender 0-2 / 2-4 / 4-6 / 6+ ft | 10.8 / 22.7 / 19.8 / 46.8% | 13.6 / 34.3 / 24.4 / 27.7% | 5-11 / 28-38 / 28-36 / 22-32% |
| defender between attacker and rim | 79.4% | 86.0% | 80-97% |
| rim / short / mid / threes | 36.5 / 12.8 / 7.7 / 43.0% | 35.4 / 13.0 / 10.1 / 41.5% | 28-36 / 12-20 / 9-16 / 38-44% |
| corner threes, share of threes | 33.0% | 30.4% | 20-28% |
| shots with 24-18 / 7-0 s on the shot clock | 10.9 / 15.6% | 12.0 / 16.7% | 14-24 / 16-26% |
| passes per half-court possession | 2.64 | 2.64 | 2.5-3.6 |
| acceleration 99th percentile | 20.0 ft/s² | 20.0 | 8-22 |
| distance per player per 48 min | 3.83 mi | 3.73 | 3.1-3.6 |
| ok / watch / off | 13 / 8 / 5 | 19 / 6 / 1 | |

Full games, 64 per league (`measure --games 64 --jobs 2`, seeds 0 / 1 / 2):

| | before | after | NBA 2025-26 |
|---|---|---|---|
| pace | 96.7 / 98.3 / 99.9 | 96.9 / 98.9 / 101.2 | 99.4 |
| points | 114.7 / 119.1 / 118.5 | 111.2 / 116.2 / 116.7 | 115.6 |
| points per 100 | 118.3 / 120.4 / 118.4 | 113.9 / 117.5 / 115.2 | 115.8 |
| eFG | .584 / .581 / .590 | .553 / .548 / .563 | .546 |
| 3P% | .38 / .38 / .38 | .36 / .36 / .35 | .36 |
| three share | .406 / .425 / .421 | .396 / .426 / .420 | .415 |
| turnovers per 100 | 14.3 / 13.3 / 13.8 | 14.8 / 13.4 / 13.8 | 14.6 |
| FTA per 100 | 23.4 / 23.9 / 22.1 | 25.2 / 26.8 / 23.5 | 23.6 |
| ORB% | .254 / .270 / .229 | .258 / .294 / .237 | .260 |
| within tolerance (of 13) | 12 / 11 / 10 | 12 / 11 / 11 | |

A full game takes ~1.05 s (was ~0.9): off-ball tracking, braking and the second
separation pass, plus more passes and decisions per possession.

Still off:
* **0-2 ft (13-14%) and 4-6 ft (24-25%).** Nearly all very tight shots are at exactly
  1.6 ft, the engine's personal space. They are drives, post-ups and catches at the rim
  with a defender in body contact, plus some pull-ups. Changing where the helper or the
  on-ball man aims didn't change them: the attacker runs into the defender.
* **Corner threes (30-34% by league).** About a quarter of all threes are taken by men
  whose spot is a corner. That is the 4-out power forward and the 5-out center, and
  62% of the bigs' threes come from there. Respacing, lifts and transition lanes trim
  only 2-3 points. Drives end in kick-outs ~70% of the time, and the corner is the
  natural outlet.
* **Early shots** (24-18 s) stay ~11-12%. Set plays start with ~19.5 s left, so early
  shots come only from transition, and transition share is already on target.
* **Free throws** (23-27 per 100 against 23.6) rose with the tighter contests at the
  rim, and seed 0 runs ~4 points a game short (its pace is the lowest, as before).
  The tests' shout bars were re-measured on 48 games. The shout samples grew from 12
  to 24 games because the shot-mix shouts now move by +0.02 to +0.09 per 12-game batch.

### Where the defence picks up
On a walk-up (after a make, or a team that doesn't push), the defence gets back and waits
instead of picking up the ball full court:
* **Man or switch:** the ball is met around half court (50 ft from the basket) and the
  others match up in their own half (40 ft).
* **Zone:** sits back near the top of the key (36 and 30 ft).
* **Press:** picks up the ball and denies the inbound full court.

On a fast break, defenders still sprint back and stop the ball wherever it is.
Backcourt side-out inbounds are let in unless the defence presses; frontcourt ones are
still denied. Called plays out of a timeout fall back to the nearest available teammate,
so the play the coach draws up nearly always runs.

Over 24 games against the old behaviour:
* **Pace:** 103.8 to 102.1, now within tolerance of the NBA's 99.4.
* **Points:** 114.2 per team (target 115.6; the 1.5-point dip is within sample noise).
* **Transition:** 1.22 points per possession (target 1.26).
* **Calibration targets in tolerance:** 9 of 13, up from 7.
* **Room on walk-ups:** a man defence leaves the handler ~17 ft in the backcourt, a
  press ~5 ft.

### Pace: denial

A set defence often overplays a shooter one pass away. On a swing, the receiver's
defender denies with probability `deny_rate` = 0.45, scaled up for good perimeter
defenders and good shooters, ×1.5 in a press and never in a zone. He sits in the
passing lane, so a forced pass there isn't routine. The receiver V-cuts (toward
the rim, then back out) and the pass waits until he's free, which takes 0.6–2.8 s
depending on his speed and IQ against the defender's. If that outlasts the
action's 3.5 s, the action dies. Set-play reads also follow the coach's
three-point appetite: high three-point-rate and perimeter-focus teams take a
catch-and-shoot three during the set a little more readily.

Shot timing was not the problem: rough NBA shot-clock splits put the average shot
at ~11.8 s remaining, and half-court first shots here come at ~11 s. The gap was
~0.5–0.9 s of game clock per possession spread across possession types. Denial is
worth about a possession a game: full-game pace 103.9 → 103.0 (deny 0.8 would give
~102, but that's more denial than NBA defences play).

### Full-game three-point volume

Full games took ~28 threes a team (NBA ~37), a share of .326 against .415, below
the half-court yardstick. A breakdown by possession type (30 games):

| per team-game | FGA | three share | rim share |
|---|---|---|---|
| set half court | 65.8 | .349 | .333 |
| flows out of transition, no advantage | 7.8 | .172 | .507 |
| flows out of transition, numbers / kick-out | 1.7 | .308 | .462 |
| breakaways | 2.9 | .011 | .724 |

**Fatigue was the main cause.** It took a flat amount off every make chance (about
4 points when tired): ~11% of a three's value but ~6% of a layup's, so tired
lineups abandoned the three. It is now proportional (make chance × (1 − k·tiredness)),
which lifts the set half-court share to .374, close to the .383 with fatigue
switched off.

Tried and reverted: pulling no-advantage pushes out into the half-court set (60% of
the time). It raised the three share only to .353, but cut transition to 11.7% of
possessions at 1.17 points.

Result (60 games): 31.5 threes a team (share .356), points per 100 110.8 → 111.9,
115.7 points a team. The rest of the gap is engine-wide: the half-court yardstick's
own three share is .36–.42, and transition flows go to the rim.

### Calibrating on full games (no change adopted)

`calibrate.py --objective fullgame --games 36` scores each candidate on full games
in the training leagues against the 2025-26 averages (`FULLGAME_TARGETS`, which now
include three share .415, eFG .546 and ORB% .260). Pace, points per 100, eFG and
three share carry the most weight. The defaults scored 3.38 ± 0.72 over 4 seeds:
pace 104–106, points per 100 112–116, eFG .545–.558, three share .29–.32, ORB%
.24–.26.

A refinement (sigma 0.10, 16 generations, 24 min) accepted a single step: a 0.06 ft
change in drop depth, which is noise. All 45 real mutants scored worse than the
defaults, and `entry_actions` sat at its upper bound again. The remaining
full-game gaps (pace ~4 high, three share ~6 points low) don't respond to these
settings. They need mechanisms.

### Full-game three-point volume, part 2: who shoots, from where, and how

Diagnosis (120 full games over leagues 0 and 1, per team-game, before any change):

| possession type | FGA | three share | 3P% |
|---|---|---|---|
| set half court (walk-ups, inbounds) | 68.2 | .331 | .340 |
| flows out of transition, no advantage | 8.8 | .154 | .364 |
| flows, numbers / kick-out | 2.5 | .274 | .488 |
| breakaways | 3.3 | .011 | — |

| position | 3PA per 36 | 3P% | NBA-like 3PA per 36 (rough) |
|---|---|---|---|
| PG | 3.6 | .275 | ~6.5 |
| SG | 6.2 | .360 | ~7 |
| SF | 5.6 | .355 | ~6 |
| PF | 2.8 | .325 | ~5 |
| C | 0.5 | .184 | ~2.5 |

- **The volume gap was players, not wings.** Wings already shot NBA volumes. Point
  guards, power forwards and centres together were ~9.5 of the ~11 missing threes
  a game. Generation made point guards worse shooters than shooting guards (mean
  three-point rating 9.4 against 11.2), PFs averaged 8.3 and 82% of centres were
  rated 7 or lower. The NBA has many shooting bigs and pull-up point guards.
- **Shot quality was fine.** 45% of threes were wide open and they went in at
  38.4% (NBA tracking ~39%). Contested threes matched the tracking numbers too.
- **Off-the-dribble jumpers were far too hard.** Catch-and-shoot threes went in at
  37.5% (NBA 37.4%) but off the dribble at only 27.2% (NBA ~33-34%), and off-the-dribble
  mid-range jumpers at 30% (NBA ~40%). The intended 3-point gap had become 5.5 (a
  +2 catch bonus and a -3.5 off-dribble penalty). On top of that, the pull-up option
  was *valued* as a shot on the move (another -3) but *taken* as a set shot.
- **Transition lanes were inside the arc.** Wings ran to (±21, 6), inside the 22-ft
  corner line, and the trailer to (7, 27), 22.9 ft from the rim. Early kick-outs and
  trail threes were long twos. In the half court the runners then left those spots
  to re-form the set.
- **Measurement.** Basketball-Reference estimates possessions from the box score
  (FGA + 0.4 FTA - 1.07 ORB% x misses + TOV). The sim counts them. The counted
  number runs ~2% higher (end-of-period possessions with no shot, and the formula's
  FT-trip weight), so the counted points per 100 read ~2 low against the targets.
  `measure` now also prints `pace_bref_formula` and `points_per_100_bref_formula`.
  The targets and their keys are unchanged.

Changes:
- **Generation** (`generate.py`): the PG template's three-point offset goes from +1 to
  +2. `STRETCH_SHARE` draws 50% of PFs and 35% of Cs as stretch bigs. Mean
  three-point ratings are now PG 10.4, SG 11.3, SF 10.3, PF 10.7, C 6.9 (was 9.4,
  11.2, 10.7, 8.3, 4.4).
- **Transition lanes** (`transition.py`, `OpenCourt.assign_lanes`/`hand_over`): wings
  fill the corners behind the arc (`CORNER_LANE`). A trailing big rated 10+ from three
  trails to the top of the arc (`TRAIL_LANE`); others go to the elbow. After a push
  without a breakaway, the half court keeps the runners on those spots (the new
  `spots` start option, `OpenCourtTuning.early_spots`).
- **Off-the-dribble jumpers** (`sim.py`): `off_dribble_penalty` moves to
  `SpatialParams` at 0.01 (a 3-point gap to catch-and-shoot, as the NBA tracking
  split). The pull-up is valued as it is shot (`pullup_on_the_move` = 0).
- **Relocation** (`HalfCourtGame.relocate_spot`): when a shooter's man (three-point
  rating 10+, `relocate_shooter`) leaves him to help on a drive, he slides 18° along
  the arc away from him (drift/lift) instead of standing still.
- `LEGACY_PARAMS` turns the new half-court behaviour off. With the old `generate.py`
  it still reproduces the original pinned possessions exactly.
- **Statistical engine** (`engine.py`): the new rating population gave it 39.5 3PA a
  game (target 35.1). The base three mix went from 0.40 to 0.33 and the base make from
  0.36 to 0.355. All 17 stats are back in range (3PA 35.6, 3P% .365, FG% .473,
  points 113.4).

The values were retrained after the changes. Full games, 240 per league:

| | before seed 0 / 1 | after seed 0 / 1 | NBA 2025-26 |
|---|---|---|---|
| pace | 102.2 / 102.6 | 103.5 / 103.6 | 99.4 |
| points per team | 114.3 / 111.2 | 113.4 / 114.1 | 115.6 |
| points per 100 (counted) | 111.6 / 108.1 | 109.4 / 109.9 | 115.8 |
| points per 100 (B-R formula) | 113.3 (40 games) / — | 111.5 / 112.1 | 115.8 |
| three share | .341 / .280 | .384 / .389 | .415 |
| 3PA per team | 29.1 / 23.8 | 33.1 / 34.2 | ~37 |
| 3P% | ~.34 | .35 / .35 | ~.36 |
| eFG | .550 / .535 | .554 / .545 | .546 |
| ORB% | .248 / .240 | .210 / .237 | .260 |
| FTA per 100 | 25.5 / 25.3 | 21.9 / 23.0 | 23.6 |
| fouls per 100 | 20.1 / 19.9 | 18.1 / 18.6 | 20.0 |
| turnovers per 100 | 15.8 / 16.2 | 15.8 / 15.4 | 14.6 |
| transition share / points | .143, 1.23 / .152, 1.19 | .152, 1.22 / .156, 1.22 | .15, 1.26 |
| starter / top minutes | 30.4, 34.1 / 30.7, 34.8 | 30.6, 34.0 / 30.5, 34.4 | 33, 36 |

By position (120 games, leagues 0-1): PG 6.1, SG 6.7, SF 4.9, PF 4.7, C 1.0 threes per
36. Flows out of transition: three share .154 → .21 (no advantage) and .27 → .41
(numbers). Off-the-dribble threes are now ~30% of all threes at ~30-32%.

What didn't work or is still off:
- **Points didn't rise.** Threes replaced drives at about the same eFG, and drives
  are what draw fouls and offensive rebounds: FTA per 100 fell 25.5 → 22 and ORB%
  fell (league 0: .248 → .210). Points per 100 stay ~110 counted (~112 by the B-R
  formula) against 115.8. The rest of the gap is fewer FGA per possession than the
  NBA: turnovers ~1 per 100 high, and too few offensive rebounds.
- **Offensive rebounds on threes** are ~21-22% (NBA estimates ~25%). Tried and
  reverted: longer caroms on long misses, with the defence's box-out edge fading for
  balls landing away from the rim. ORB% on missed threes went only .212 → .219.
- **Pace went up ~1** (more early catch-and-shoot threes).
- **Centres** still shoot only ~1 three per 36 (NBA ~2.5). A stretch centre rated
  10-11 still sets up in the dunker spot unless he's 12+ (`five_out`).
- **The half-court yardstick** (held-out league 0, starters only) now overshoots on
  threes: share .475 (was .396, estimate .42), points per possession 1.11 (1.155),
  ORB .18 and FTA/FGA .17 (both below estimates). The full-game numbers are real
  data, so they decide.

## Coaching systems (`hoopsim/systems.py`, web: **Tactics**)
Presets like NBA 2K's coach settings. Choosing a system **sets the sliders** (pace,
three-point rate, crash the glass, offensive focus, defensive scheme), which you can
still adjust. While it's selected, it also keeps a **style** that both engines play:
the shots it hunts, how the ball is shared, how often it runs, which set plays it
calls and how many, and small standing nudges to players' decisions (like a coach's
shouts, smaller).

| Offence | Identity |
|---|---|
| Balanced | No strong identity |
| Pace and Space | Spread pick-and-roll, threes, few mid-range shots, runs a bit |
| Seven Seconds or Less | Pushes after rebounds, steals and makes; shoots early; fewer sets |
| Motion (Princeton) | Hand-offs, down-screens, swings; shots spread around; most assists |
| Triangle | Post entries, cuts, mid-range reads; more sets |
| Grit and Grind | Slow, inside, crashes the glass, doesn't run |
| Inside-Out | Feeds the post big, kicks out when he draws help |
| Heliocentric | The star runs pick-and-roll and takes his shots; fewest assists |

| Defence | Identity |
|---|---|
| Balanced man | The default |
| Switch everything | Switches screens: no open pick-and-roll looks, mismatches to hunt |
| Drop coverage | The big drops and everyone sags: fewer shots at the rim, more pull-up threes |
| Zone | Packs the paint |
| Full-court press | Picks up full court (see "Where the defence picks up"), forces turnovers, tires legs |

**Fit.** The ★ ratings show how well each system suits your roster, and AI teams pick
what suits theirs: shooters go Pace and Space, a dominant post big gets Inside-Out or
the Triangle, a star guard runs Heliocentric. In the real-NBA league Denver gets the
Triangle (Jokić) and Oklahoma City goes Heliocentric. League files keep their teams'
real pace and three-point settings, and a file can name a team's systems
(`"offense_system"`, `"defense_system"`).

The wrong system hurts. Inside systems cost a team of shooters with ordinary bigs
several points per 100 possessions, while for a strong post team they match or beat
Balanced. In a live match the Coach panel switches systems for that game only. The
CLI command is `tactics system=triangle defense_system=drop`.

**Measured.** These numbers come from the same team, opponents and seeds, with only
the system changed (statistical engine, 400 games each):

| System | Possessions | Three-point share | Star's share of shots |
|---|---|---|---|
| Balanced | 99 | 47% | 21% |
| Pace and Space | 103 | 57% | |
| Seven Seconds or Less | 104 | | |
| Grit and Grind | 94 | 33% | |
| Triangle | | 39% | |
| Heliocentric | | | 29% |

Drop coverage raises opponents' three-point share by about 3 points. With every AI team
on a system, the statistical league stays calibrated: 98.8 possessions, 111 points and
42% threes, against 98.4, 111 and 42% all-Balanced. The spatial engine runs about 2
possessions a game faster (104 against 102; the NBA is 99.4); points stay on target.

### Scoring options (Tactics → Scoring options)
Name a 1st, 2nd and 3rd scoring option, or leave any of them blank to let the game
flow. With all three blank, play is exactly as before.

**What an option changes:**
* **Statistical engine:** his weight when the team picks a shooter goes up (×1.45 /
  ×1.25 / ×1.12 for the 1st / 2nd / 3rd).
* **Spatial engine:** teammates value passing to him more and he values his own shot
  more. This is a standing, smaller version of the "get him the ball" shout, so a good
  defence can still take him away.
* **Late in the clock:** when the team holds for a last shot, the 1st option's share
  is boosted a further 2.5×.
* **When an option sits:** if he's injured or on the bench, the next option moves up.
  A Heliocentric offence's star is the 1st option if you set one.

**Where to set it:** the live Coach panel changes the options for one game; the CLI
command is `tactics options=12,34,56` (`options=` clears them).

**Measured** on a fixed team, with only the options changed:
* **Making the 4th-best starter the 1st option:** his share of the team's shots goes
  from 15% to 20% (statistical engine, 600 games) and from 9% to 13% (spatial engine,
  36 games).
* **Team efficiency** stays the same within noise: on the spatial engine, feeding the
  top three scored 103.8 points per 100 possessions against 101.9 without options
  (60 games).
* **Turnovers** rise by about 1 per 100 possessions, from forcing the ball to them.

## All-Star Weekend (`hoopsim/allstar.py`, web: **League → All-Star**)
Seasons long enough for an All-Star break (60+ games) get the NBA's mid-February
weekend. Everything happens on its date as you sim:

| Days before the game | Event |
|---|---|
| 42, 35, 28 | Fan voting returns: the leading vote-getters by conference and position (inbox) |
| 24 | **Starters:** two guards and three frontcourt players per conference, ranked half on the fan vote, half on the players' and media vote |
| 17 | **Reserves:** the coaches' picks (two guards, three frontcourt, two wild cards); the inbox names your All-Stars and any snub |
| 14 | **Rising Stars** teams and the **Three-Point Contest** field |
| 3 | Injury replacements by the commissioner (a reserve moves into an injured starter's spot; injured shooters are replaced by the next in line) |
| 2 (Fri) | **Rising Stars**: Rookies against Sophomores (a new league without both draws two teams of young players) |
| 1 (Sat) | **Three-Point Contest**: five racks of five (the last ball of each rack is a 2-point money ball, one rack is all money balls) and two 3-point deep shots: 40 points at most. Eight shooters, the top three reach the final, ties go to a shoot-off. |
| 0 (Sun) | **All-Star Game**: East against West, with its MVP |

* **How players are chosen.**
  * **Fans** love scorers, winners, big markets, star power (ability) and past All-Stars.
  * **Players, media and coaches** look at Game Score per game, team record and ability.
  * A player needs to have played 40% of his team's games.
* **The exhibitions** are played on copies of the players, so there are no injuries
  and no effect on season stats. All-Star defence is relaxed (lower defensive ratings)
  and the pace is flat out. All-Star Games average about 170-175 points per team on
  either engine, and Rising Stars a little less. On the spatial or hybrid engine both
  games are recorded, and **Watch** opens them in the match viewer.
* **The weekend's effect on the season.** Voting and the weekend draw on their own
  random stream, so they never change the season's results by themselves. Being
  selected does lift a player's morale.
* **Honours** go on the season's career line (`all_star`, `asg_mvp`,
  `three_point_champion`, `rising_stars_mvp`). They appear on the player card
  ("3× All-Star"), in League → History, and under past weekends on the All-Star
  screen. `python -m hoopsim season` prints them with the awards.
* **Calendar** marks the announcements and the weekend's events.
* **API:** `GET /api/allstar`. Exhibition replays are served at `/api/replay/10000001` (the
  game) and `/api/replay/10000002` (Rising Stars).

## Real leagues and the editor

### League files (`hoopsim/league_file.py`)
A league file is JSON listing teams and players with their ratings; hoopsim fills in
ids, lineups, missing contracts and the schedule. Everything except names, team
abbreviations and positions is optional (missing ratings are 10). The format is at the
top of `league_file.py`.

```bash
python -m hoopsim new --league leagues/my-league.json --out league.db
python -m hoopsim play --league leagues/my-league.json
python -m hoopsim season --league leagues/my-league.json
```

In the web app, files in `./leagues` (or `--leagues DIR`) appear under **New game →
League**. Files are picked by name only, never by path.

### The real NBA, for personal use (`tools/build_nba_league.py`)
`tools/build_nba_league.py --fetch` builds `leagues/nba-2026-27.json` from
Basketball-Reference: the current 30 rosters, real conferences and divisions, contracts
with options, and ratings derived from stats. Fetching takes about 40 pages, cached in
`leagues/cache/`, one every 3.5 seconds, with an honest User-Agent. `leagues/` is
gitignored. Real names and data never ship with the game. Keep the file for your own
use.

How ratings are derived:
* **Stats:** this season (early, few games), last season and the one before, which
  counts at half weight. They are blended by minutes. Rates are shrunk toward average by
  sample size (attempts or minutes), so ten hot games don't make a shooter. A player who
  missed last season through injury is judged on the season before.
* **Shape:** each attribute gets a z-score among NBA players from the stats that show
  it. For example: three-point % and volume, rim/short/mid accuracy by distance, assist,
  steal, block and rebound percentages, defensive box plus/minus, size, dunk share.
  These z-scores are then scaled to the spread of generated leagues.
  * **Shot load.** The engines give shots to the best scorers, so usage rate is added to
    the scoring ratings (half for threes). A 30%-usage star out-shoots a 15%-usage
    spot-up shooter with the same percentages.
  * **Shares on a log scale.** Rebound and assist rates count on a log scale, so the
    top bigs and playmakers aren't pushed to the rating cap. Part of a player's height
    is taken out of his rebound ratings, because the engines already favour tall
    rebounders.
  * **Shot tendencies are what ratings don't explain.** A great shooter's rating
    already gets him more threes, so his three tendency is set so that the statistical
    engine reproduces his real mix of rim, short, mid-range and three-point attempts.
* **Level:** overall ability comes from impact (shrunk box plus/minus and minutes,
  with salary as a prior when there are few minutes). It is mapped onto the ability
  distribution of a generated league. A real league plays like a generated one (median
  rostered ability 93, 90th percentile ~125), so the engines stay calibrated.
* **Other attributes:**
  * potential from age;
  * shot tendencies as above; passing and cutting tendencies from assist rate and
    assisted shots;
  * injury proneness partly from games missed;
  * physique from the roster's height and weight;
  * salary rules approximately 2026-27 (cap $165M, tax $201M, minimum $1.27M, MLE
    $14.1M).

**Checked against a real season.** Three simulated seasons on the statistical engine,
compared with 2025-26 (players with 30+ games and 15+ minutes):

| | before | after |
|---|---|---|
| points per game, correlation | 0.78 | 0.87 |
| top-60 scorers, points bias | −2.0 | +0.4 |
| top rebounder / assist man (per game) | 20.3 / 12.9 | 13.4 / 10.3 |
| team wins vs 2025-26, correlation | 0.63 | 0.76 |
| team wins vs 2025-26, mean miss | 9.1 | 7.9 |

The best scorers still average close to 40 points, as in generated leagues; that is the
engine's. (The minutes shape, which gave about 130 players 30+ minutes, is fixed: see
"Minutes" below.)

**Minutes** (`hoopsim/lineup.py` `plan_rotation`). The coach shares out the 240 minutes by
ability rank, not starting spot, along the 2025-26 NBA curve (a team's best player about
34, then 31.5, 29.5, 28, 26, 24, 21.5, 19.5, 17.5). A player far better than his
teammates gets more, up to 38; a starter counts as 12 ability points better when
ranking, so a strong sixth man can still out-play a weak starter. Over a simulated
season (players with 40+ games):

| | NBA 2025-26 | before | now |
|---|---|---|---|
| players with 30+ mpg | 71 | 132 | 79-86 |
| 34+ / 36+ mpg | 18 / 3 | 23 / 0 | 18-26 / 2-3 |
| most mpg | 38.0 | 35.8 | 36.5-36.7 |
| team's 1st / 3rd / 5th / 8th man (mpg) | 33.6 / 29.7 / 26.2 / 19.6 | 34.2 / 32.7 / 29.9 / 19.5 | 34.4 / 30.2 / 27.5 / 20.9 |

The statistical engine stays 17/17 within tolerance and the spatial measure 13/13 (seed
1; the starter-minutes target is now the real ~31 in games started, from an estimate of 33).

**Career history.** With history on (the default), `--fetch` also gets every season since
2003-04, one page per season, plus every draft since 2003. That is about 45 more pages,
fetched once. Each player gets:
* one line per season, regular season and playoffs (a traded player's combined line
  under his last team; old codes such as SEA and NJN are kept and linked to today's
  franchise);
* his real draft year and pick (undrafted players arrive in their first season);
* honours: All-Star, MVP, DPOY, ROY, Sixth Man, MIP, Clutch POY, and All-NBA and
  All-Defensive teams.

The effects:
* The player card shows his career table and charts from his rookie season, with honour
  codes per season (hover a code for its full name) and career honour badges.
* Rookie of the Year and Rising Stars use the real draft classes.
* Most Improved compares a player with his real previous season.
* Past All-Star selections count in the fan vote.
* Scouts are surer about veterans with long track records.

Seasons played in the game add to the same history, including awards. Use `--no-history`
to skip it.

Standard roster spots are capped at 15. Two-way players and extras become free
agents, along with veterans unsigned since last season.

### In-game editor (`hoopsim/editor.py`, web: **Editor**)
Like Football Manager's editor, it shows **true** ratings, with no scouting fog. You
can change any player's:
* ratings, hidden ratings and tendencies (1-20 sliders);
* potential (never below current ability);
* name, birth date, nationality, position(s), height, wingspan, weight and jersey;
* morale, an injury (heal it), contract (salary, end season, option) and team.

You can also change a team's city, name, abbreviation and reputation.

The player card has an **Edit** button. Edits go through the game's own limits, so an
edited league is still legal:
* roster minimum and maximum;
* unique jersey numbers;
* maximum salary.

An invalid edit changes nothing. Moving players redoes the affected lineups. Each edit
is logged in the save (`league.info["edited"]`), and the app shows that the editor was
used. API:
* `GET /api/editor/players?team_id=|free_agents=|q=`;
* `GET`/`POST /api/editor/player/{id}`;
* `POST /api/editor/team/{id}`.

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
physical attributes first. Point guards shoot threes about as well as shooting
guards, and half of power forwards and a third of centres are drawn as stretch bigs
(+5 three-point, +1 mid-range, -1 close range on the template; the level shift still
lands them on their target CA). Salaries follow a steep curve on CA. Rosters are filled
by a snake draft with noisy scouting, so there are strong and weak teams without
absurd gaps. `auto_lineup` (in `lineup.py`) brute-forces the best starting five and
shares out 240 minutes. The schedule is NBA-shaped (see below).

## Schedule (`hoopsim/schedule.py`)

Every regular season follows the NBA calendar:

- Opening night is a Tuesday in late October, and the season ends on a Sunday in
  mid-April (about 173 days, on ~166 game nights; shorter seasons are compressed).
- There are no games on Thanksgiving or Christmas Eve.
- Christmas Day has a slate of up to 5 games.
- The six-day All-Star break runs around the third Sunday of February.
- The trade deadline falls about 60% of the way through.
- League nights follow the NBA's weekday rhythm, with Wednesday and Friday busiest
  and Monday and Thursday lightest.

Every team plays exactly `games_per_season` (82) games, **41 at home and 41
away**.

**Divisions.** Teams are in the NBA's six divisions: Atlantic, Central and
Southeast in the East; Northwest, Pacific and Southwest in the West. A 30-team
league has five teams in each, like the NBA. Conferences of 4–8 teams use two
divisions each (Atlantic/Central, Northwest/Pacific) and smaller ones one.
Existing saves keep their old divisions.

- **Opponents follow the NBA formula.** In a 30-team league that's 4 games
  against each of the 4 division rivals, 4 against six other conference teams and
  3 against the other four, and 2 against each of the 15 teams in the other
  conference (16 + 36 + 30 = 82). Smaller leagues scale it: in a 12-team league,
  11 against division rivals, 10 against the rest of the conference and 5 against
  the other conference. Leagues whose divisions can't be equal (e.g. 10 teams) get
  an even split.
- Rest is NBA-like: back-to-backs are ~18% of the gaps (**~15 a team**), one rest
  day ~64%, two ~12%, and a few longer breaks. A team never plays three nights
  running.
- Road trips run to at most 6 games and homestands to at most 7.

Games are placed night by night, greedily:

- Teams that are behind on their games are scheduled first.
- A back-to-back quota keeps each team near the NBA rate through the season.
- The same two teams don't meet again within a few days.

`schedule_report` gives per-team checks.

**Standings and tiebreakers** (`playoffs.ranked`). Teams with the same winning
percentage are separated in the NBA's order:

- **Two teams:** head-to-head, then division winner, then division record (same
  division only), then conference record.
- **Three or more:** division winner first, then head-to-head among all the tied
  teams, then division record, then conference record.

Point differential follows in both cases. When a step splits the tied teams, each
group starts again from the top. The NBA's "record against playoff teams" steps
are left out, and point differential settles those cases. The same procedure picks
each division's winner and the playoff seeds. The League screen's standings have
a **Conference / Division** toggle, with division leaders marked ⓓ and DIV and
CONF record columns. Rest matters in both engines: on the
second night of a back-to-back, players who logged 10+ minutes the night before
start the game tired (`Tuning.b2b_start_energy` = 0.85) and shoot slightly worse
(`b2b_form` = 0.975). Over six 30-team statistical seasons, tired teams against
rested ones won 46.4% and lost by 1.2 points (NBA: roughly 1.5). Playoffs start six
days after the regular season (the play-in week) and are played every other day.

The browser app's **Calendar** screen shows the season a month at a time:

- Each day shows your games (home/away, result, a **B2B** badge on second
  nights), how many league games are on, and markers for opening night,
  Thanksgiving, Christmas Eve and Day, the All-Star break and Game, the trade
  deadline, the final day and the playoffs.
- Summary cards show games, home/away split, back-to-backs and the longest road
  trip. Any team's schedule can be viewed.
- Clicking a day lists every game on it, with box score and watch links.
- **Sim to this day** plays up to it. It stops early for a trade offer, as the
  other sim buttons do.

The routes are `/api/calendar`, `/api/calendar/day/{day}` and `POST /api/sim/to`.

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
