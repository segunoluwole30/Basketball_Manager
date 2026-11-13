"""Season games on the spatial engine.

A league whose ``rules.match_engine`` is ``"spatial"`` plays its scheduled games
(regular season, playoffs, the web interface, ``play``) as full spatial games
(``fullgame.py``) instead of the statistical engine. The result is the same
``GameSim`` (score, box score, events, injuries), so standings, stats, awards,
morale and development work unchanged.

Injuries and game-day form work as in the statistical engine (``FullGame``):
injuries are rolled at dead balls from court time, injury proneness and fatigue,
at the same rate, and a hurt player leaves the game; morale and a random night
shift each player's make chances.

Differences from the statistical engine, for now:
* A spatial game takes about 0.7 s (a statistical one a few milliseconds), so a
  1,230-game season takes about 15 minutes.
"""
from __future__ import annotations

import json
import random
import zlib
from typing import Optional

from ..engine import GameSim
from ..models import Game, League
from .fullgame import FullGame
from .values import DecisionModel, default_model

_MODEL: Optional[DecisionModel] = None


def _model() -> DecisionModel:
    global _MODEL
    if _MODEL is None:
        _MODEL = default_model()                 # load the decision values once
    return _MODEL


def simulate_spatial(league: League, game: Game, rng: random.Random, record: Optional[bool] = None) -> GameSim:
    """Play ``game`` as a full spatial game, without changing the league. ``record``
    (default: the user's team is playing) keeps the movement for the viewer."""
    if record is None:
        record = league.user_team_id in (game.home_team_id, game.away_team_id)
    fg = FullGame(league, game.home_team_id, game.away_team_id, rng, record=record, model=_model(),
                  day=game.day)
    fg.run()
    return finish_spatial(fg, rng, record)


def finish_spatial(fg: FullGame, rng: random.Random, record: bool) -> GameSim:
    """Post-game for a finished spatial game: the replay. (Injuries were rolled
    during the game, ``FullGame.check_injuries``, including a last check for court
    time after the final dead ball; ``rng`` is kept for callers.)"""
    sim = fg.result
    replay = None
    if record:
        from .export import viewer_data
        replay = zlib.compress(json.dumps(viewer_data(fg), separators=(",", ":")).encode(), 9)
    return GameSim(sim.result, sim.box_score, sim.play_by_play, list(sim.injuries), replay)


def replay_data(league: League, game_id: int) -> Optional[dict]:
    """The viewer data of a recorded game (None if it wasn't recorded or was dropped)."""
    blob = league.replays.get(game_id)
    return json.loads(zlib.decompress(blob)) if blob else None
