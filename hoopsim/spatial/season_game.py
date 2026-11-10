"""Season games on the spatial engine.

A league whose ``rules.match_engine`` is ``"spatial"`` plays its scheduled games
(regular season, playoffs, the web interface, ``play``) as full spatial games
(``fullgame.py``) instead of the statistical engine. The result is the same
``GameSim`` (score, box score, events, injuries), so standings, stats, awards,
morale and development work unchanged.

Differences from the statistical engine, for now:
* Injuries are rolled after the game from each player's minutes, injury
  proneness and how tired he finished, at the statistical engine's rate. A player
  hurt in the game doesn't leave it early.
* Morale doesn't change game-day form.
* A spatial game takes about 0.7 s (a statistical one a few milliseconds), so a
  1,230-game season takes about 15 minutes.
"""
from __future__ import annotations

import math
import random
from typing import Optional

from ..engine import DEFAULT_TUNING, GameSim
from ..injuries import draw_injury
from ..models import Game, League
from .fullgame import FullGame
from .values import DecisionModel, default_model

_MODEL: Optional[DecisionModel] = None


def _model() -> DecisionModel:
    global _MODEL
    if _MODEL is None:
        _MODEL = default_model()                 # load the decision values once
    return _MODEL


def simulate_spatial(league: League, game: Game, rng: random.Random, record: bool = False) -> GameSim:
    """Play ``game`` as a full spatial game, without changing the league."""
    fg = FullGame(league, game.home_team_id, game.away_team_id, rng, record=record, model=_model())
    sim = fg.run()
    t = DEFAULT_TUNING
    injuries = []
    for side in fg.sides.values():
        for s in side.roster:
            if s.seconds <= 0:
                continue
            # Same rate as the statistical engine: per 36 minutes, more for the
            # injury-prone and for players who finished tired.
            rate = (t.injury_rate_per_36 / 2160 * math.exp(0.12 * (s.p.hidden.injury_proneness - 10))
                    * (1 + 1.5 * (1 - s.energy)))
            if rng.random() < 1 - math.exp(-rate * s.seconds):
                injuries.append((s.p.id, draw_injury(rng)))
    return GameSim(sim.result, sim.box_score, sim.play_by_play, injuries)
