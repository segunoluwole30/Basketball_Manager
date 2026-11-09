"""Possession-by-possession match engine.

Each possession runs through:

    clock -> turnover? -> non-shooting foul? -> shot (who, where, defender)
          -> block? -> shooting foul? -> make? -> assist / rebound -> ...

Every probability starts from a league-average base rate (``Tuning``) and is
nudged by the attributes involved. A rating of 10 is league average, so a
lineup of all-10 players should produce league-average numbers. Run
``python -m hoopsim.calibrate`` after changing anything here.

Effective ratings fall as players tire (``energy``), so stamina, rotations and
minutes all affect the result. Hidden attributes come in through game-to-game
form (consistency) and late-game shooting (clutch).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional

from .lineup import lineup_is_usable, plan_rotation
from .models import (
    BoxScore, DefensiveScheme, Game, GameResult, League, OffensiveFocus, Player, PlayerLine,
    Position, Team, TeamBox,
)

POSITIONS = list(Position)
RIM, CLOSE, MID, THREE = "rim", "close", "mid", "three"
ZONES = (RIM, CLOSE, MID, THREE)
ZONE_TEXT = {RIM: "layup", CLOSE: "floater", MID: "jumper", THREE: "three"}


@dataclass
class Tuning:
    """League-wide base rates. Calibrated so all-10 rosters look like a modern pro league."""

    seconds_per_possession: float = 14.8   # until the first shot/turnover
    possession_sd: float = 4.5
    turnover_rate: float = 0.143
    steal_share: float = 0.55              # turnovers that are steals
    non_shooting_foul_rate: float = 0.10
    offensive_foul_share: float = 0.15     # dead-ball turnovers that are charges/illegal screens
    team_rebound_share: float = 0.11       # misses out of bounds etc.: no player credited
    zone_mix: dict = field(default_factory=lambda: {RIM: 0.31, CLOSE: 0.15, MID: 0.13, THREE: 0.41})
    make: dict = field(default_factory=lambda: {RIM: 0.665, CLOSE: 0.455, MID: 0.43, THREE: 0.356})
    shooting_foul: dict = field(default_factory=lambda: {RIM: 0.24, CLOSE: 0.13, MID: 0.042, THREE: 0.016})
    block: dict = field(default_factory=lambda: {RIM: 0.088, CLOSE: 0.068, MID: 0.016, THREE: 0.008})
    assist: dict = field(default_factory=lambda: {RIM: 0.62, CLOSE: 0.54, MID: 0.55, THREE: 0.90})
    and_one_make_factor: float = 0.45      # make chance when fouled, relative to normal
    free_throw: float = 0.773
    defensive_rebound_edge: float = 3.1    # >1: defence wins most boards
    ft_defensive_rebound_edge: float = 7.0
    fast_break_after_steal: float = 0.45
    fast_break_after_rebound: float = 0.11
    home_make_bonus: float = 0.016
    # Teams with a big lead ease off and trailing teams push harder. Per point
    # of lead, the leader's make chance drops (and the trailer's rises) by this.
    score_effect: float = 0.0015
    score_effect_cap: float = 0.03
    # How strongly ratings move outcomes. 1.0 = raw ratings; lower compresses
    # every rating toward 10 so talent gaps produce realistic point margins.
    skill_impact: float = 0.5
    # Fatigue
    drain_per_second: float = 0.00042      # stamina-10 player on court
    recover_per_second: float = 0.0011     # on the bench
    period_break_recovery: float = 0.08
    halftime_recovery: float = 0.20


DEFAULT_TUNING = Tuning()


# --------------------------------------------------------------------------
# In-game state
# --------------------------------------------------------------------------
class _InGame:
    __slots__ = ("p", "line", "target_seconds", "energy", "on_court", "form",
                 "stint_start", "left_at", "team", "ca", "fit", "impact")

    def __init__(self, player: Player, team: "_Side", target_minutes: int, form: float):
        self.p = player
        self.line = PlayerLine(player.id)
        self.target_seconds = target_minutes * 60
        self.energy = 1.0
        self.on_court = False
        self.form = form
        self.stint_start = 0.0
        self.left_at = -1e9
        self.team = team
        self.impact = team.sim.t.skill_impact
        # Ratings don't change mid-game, so cache ability and positional fit.
        self.ca = player.current_ability
        self.fit = [(player.ability_at(pos) - self.ca) / 10 for pos in POSITIONS]

    def r(self, attr: str) -> float:
        """Effective rating after fatigue, scaled by ``Tuning.skill_impact``."""
        raw = getattr(self.p.attributes, attr) * (0.55 + 0.45 * self.energy)
        return 10 + (raw - 10) * self.impact

    @property
    def fouled_out(self) -> bool:
        return self.line.pf >= self.team.sim.league.rules.personal_foul_limit

    def __repr__(self) -> str:
        return f"<{self.p.name}>"


class _Side:
    def __init__(self, sim: "_GameSim", team: Team, players: list[Player], home: bool):
        self.sim = sim
        self.team = team
        self.tactics = team.tactics
        self.home = home
        if lineup_is_usable(sim.league, team.id):
            starters, minutes = self.tactics.starters, self.tactics.minutes_targets
        else:
            starters, minutes = plan_rotation(players, self.tactics.rotation_depth)
        rng = sim.rng
        self.players: dict[int, _InGame] = {}
        for p in players:
            # Low consistency -> wider spread of good and bad nights.
            sd = 0.02 + (20 - p.hidden.consistency) * 0.0025
            self.players[p.id] = _InGame(p, self, minutes.get(p.id, 0), rng.gauss(1.0, sd))
        self.lineup: list[_InGame] = [self.players[pid] for pid in starters]  # slot i = POSITIONS[i]
        for ig in self.lineup:
            ig.on_court = True
            ig.line.starter = True
        self.score = 0
        self.period_points: list[int] = []
        self.team_fouls = 0
        self.possessions = 0

    @property
    def bench(self) -> list[_InGame]:
        return [ig for ig in self.players.values() if not ig.on_court]


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------
@dataclass
class PlayEvent:
    period: int
    clock: float                   # seconds left in the period
    team_id: Optional[int]
    team: str                      # abbreviation, "" for neutral events
    text: str
    home_score: int
    away_score: int

    def format(self) -> str:
        """e.g. ``Q1 11:42  NGW  5-3  Tyrese Vasquez makes layup`` (score is away-home)."""
        q = f"Q{self.period}" if self.period <= 4 else f"OT{self.period - 4}"
        m, s = divmod(int(math.ceil(self.clock)), 60)
        return (f"{q:<3} {m:2d}:{s:02d}  {self.team:<4} "
                f"{self.away_score:>3}-{self.home_score:<3}  {self.text}")


@dataclass
class GameSim:
    result: GameResult
    box_score: BoxScore
    play_by_play: list[PlayEvent]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _pick(rng: random.Random, items: list, weights: list[float]):
    return rng.choices(items, weights)[0]


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


USAGE = 0.34   # how strongly better scorers dominate the shots


def _offense_skill(ig: _InGame) -> float:
    scoring = [ig.r("finishing"), ig.r("close_range"), ig.r("mid_range"), ig.r("three_point")]
    return (0.5 * max(scoring) + 0.3 * sum(scoring) / 4
            + 0.1 * ig.r("ball_handling") + 0.1 * ig.r("basketball_iq"))


# --------------------------------------------------------------------------
# The simulation
# --------------------------------------------------------------------------
class _GameSim:
    def __init__(self, league: League, game: Game, rng: random.Random, tuning: Tuning,
                 record_pbp: bool):
        self.league = league
        self.rules = league.rules
        self.rng = rng
        self.t = tuning
        self.record = record_pbp
        self.events: list[PlayEvent] = []

        def players_for(team_id: int) -> list[Player]:
            ps = [p for p in league.roster(team_id) if p.is_available]
            if len(ps) < 5:
                raise ValueError(f"team {team_id} has fewer than 5 healthy players")
            return ps

        self.home = _Side(self, league.teams[game.home_team_id], players_for(game.home_team_id), True)
        self.away = _Side(self, league.teams[game.away_team_id], players_for(game.away_team_id), False)
        self.period = 0
        self.clock = 0.0           # seconds left in the period
        self.elapsed = 0.0         # game seconds played so far
        self.regulation = 4 * self.rules.quarter_minutes * 60

    # -- logging ------------------------------------------------------------
    def log(self, side: Optional[_Side], text: str) -> None:
        if self.record:
            self.events.append(PlayEvent(self.period, max(0.0, self.clock),
                                         side.team.id if side else None,
                                         side.team.abbreviation if side else "", text,
                                         self.home.score, self.away.score))

    def other(self, side: _Side) -> _Side:
        return self.away if side is self.home else self.home

    # -- clock & fatigue ----------------------------------------------------
    def advance(self, seconds: float) -> None:
        seconds = min(seconds, self.clock)
        if seconds <= 0:
            return
        for side in (self.home, self.away):
            opp_press = self.other(side).tactics.defensive_scheme is DefensiveScheme.PRESS
            own_press = side.tactics.defensive_scheme is DefensiveScheme.PRESS
            press = 1.15 if (opp_press or own_press) else 1.0
            for ig in side.players.values():
                if ig.on_court:
                    ig.line.seconds += seconds
                    drain = self.t.drain_per_second * (1.5 - ig.p.attributes.stamina / 20) * press
                    ig.energy = max(0.3, ig.energy - drain * seconds)
                else:
                    ig.energy = min(1.0, ig.energy + self.t.recover_per_second * seconds)
        self.clock -= seconds
        self.elapsed += seconds

    # -- scoring ------------------------------------------------------------
    def score(self, side: _Side, points: int) -> None:
        side.score += points
        side.period_points[-1] += points
        for ig in side.lineup:
            ig.line.plus_minus += points
        for ig in self.other(side).lineup:
            ig.line.plus_minus -= points

    def margin(self) -> int:
        return abs(self.home.score - self.away.score)

    def clutch_time(self) -> bool:
        return self.period >= 4 and self.clock <= 120 and self.margin() <= 5

    # -- substitutions -----------------------------------------------------
    def _sub_score(self, ig: _InGame, crunch: bool, garbage: bool) -> float:
        if ig.fouled_out:
            return -1e9
        if crunch:
            return ig.ca / 10 + 6 * (ig.energy - 0.8)
        if garbage:
            return -ig.ca / 10 + 2 * ig.energy
        expected = ig.target_seconds * min(1.0, self.elapsed / self.regulation)
        deficit = (expected - ig.line.seconds) / 60
        if ig.target_seconds == 0:
            deficit -= 6
        score = 0.6 * deficit + 9 * (ig.energy - 0.8)
        allowed = {1: 2, 2: 3, 3: 4}.get(self.period, 5)
        if ig.line.pf >= allowed:
            score -= 8                     # foul trouble: sit him
        return score

    def substitutions(self, side: _Side) -> None:
        crunch = self.period >= 4 and self.clock <= 300 and self.margin() <= 8
        garbage = self.period >= 4 and self.clock <= 360 and self.margin() >= 20
        bench = [ig for ig in side.bench if not ig.fouled_out]
        if not bench:
            return
        scores = {id(ig): self._sub_score(ig, crunch, garbage) for ig in side.players.values()}
        order = sorted(range(5), key=lambda i: scores[id(side.lineup[i])])
        for slot in order:
            out = side.lineup[slot]
            forced = out.fouled_out
            if not forced and self.elapsed - out.stint_start < 240 and not (crunch or garbage):
                continue                   # let a fresh stint breathe

            def value(ig: _InGame) -> float:
                return scores[id(ig)] + ig.fit[slot]

            ready = [ig for ig in bench if self.elapsed - ig.left_at >= 120 or forced or crunch]
            if not ready:
                continue
            best = max(ready, key=value)
            if forced or value(best) - value(out) > 2.5:
                side.lineup[slot] = best
                best.on_court, best.stint_start = True, self.elapsed
                out.on_court, out.left_at = False, self.elapsed
                bench.remove(best)
                if not out.fouled_out:
                    bench.append(out)
                self.log(side, f"Sub: {best.p.name} in for {out.p.name}"
                         + (" (fouled out)" if forced else ""))

    # -- fouls ----------------------------------------------------------------
    def foul(self, defense: _Side, fouler: _InGame) -> None:
        fouler.line.pf += 1
        defense.team_fouls += 1
        if fouler.fouled_out:
            self.log(defense, f"{fouler.p.name} fouls out")
            self.substitutions(defense)

    def free_throws(self, offense: _Side, shooter: _InGame, n: int) -> bool:
        """Shoot ``n`` free throws. Returns True if the last one was missed (live ball)."""
        pct = _clamp(self.t.free_throw + 0.018 * (shooter.p.attributes.free_throw - 10)
                     + (0.004 * (shooter.p.hidden.clutch - 10) if self.clutch_time() else 0),
                     0.35, 0.95)
        made = 0
        last_missed = False
        for _ in range(n):
            shooter.line.fta += 1
            if self.rng.random() < pct:
                shooter.line.ftm += 1
                made += 1
                self.score(offense, 1)
                last_missed = False
            else:
                last_missed = True
        self.log(offense, f"{shooter.p.name} makes {made} of {n} free throws")
        return last_missed

    # -- rebounding -----------------------------------------------------------
    def _reb_weight(self, ig: _InGame, attr: str) -> float:
        return math.exp(0.25 * (ig.r(attr) - 10) + (ig.p.physique.height_cm - 203) / 24)

    def rebound(self, offense: _Side, defense: _Side, after_ft: bool) -> Optional[_InGame]:
        """Resolve a missed shot. Returns the offensive rebounder, or None if the defence got it."""
        ow = [self._reb_weight(ig, "offensive_rebounding") for ig in offense.lineup]
        dw = [self._reb_weight(ig, "defensive_rebounding") for ig in defense.lineup]
        crash = math.exp(0.05 * (offense.tactics.crash_offensive_glass - 10))
        edge = self.t.ft_defensive_rebound_edge if after_ft else self.t.defensive_rebound_edge
        if defense.tactics.defensive_scheme is DefensiveScheme.ZONE:
            edge *= 0.9                    # zones give up more offensive boards
        p_off = sum(ow) * crash / (sum(ow) * crash + sum(dw) * edge)
        team_rebound = self.rng.random() < self.t.team_rebound_share
        if self.rng.random() < p_off:
            if team_rebound:
                self.log(offense, "Team rebound")
                return offense.lineup[0]   # ball handler inbounds and goes again
            ig = _pick(self.rng, offense.lineup, ow)
            ig.line.orb += 1
            self.log(offense, f"Offensive rebound {ig.p.name}")
            return ig
        if team_rebound:
            self.log(defense, "Team rebound")
            return None
        ig = _pick(self.rng, defense.lineup, dw)
        ig.line.drb += 1
        self.log(defense, f"Defensive rebound {ig.p.name}")
        return None

    # -- shot selection -------------------------------------------------------
    def _zone_weights(self, ig: _InGame, offense: _Side, defense: _Side,
                      fast_break: bool, putback: bool) -> list[float]:
        mix = self.t.zone_mix
        drive = 0.7 * ig.r("finishing") + 0.15 * ig.r("speed") + 0.15 * ig.r("ball_handling")
        w = {
            RIM: mix[RIM] * math.exp(0.25 * (drive - 10)),
            CLOSE: mix[CLOSE] * math.exp(0.25 * (ig.r("close_range") - 10)),
            MID: mix[MID] * math.exp(0.25 * (ig.r("mid_range") - 10)),
            THREE: mix[THREE] * math.exp(0.35 * (ig.r("three_point") - 10)
                                         + 0.06 * (offense.tactics.three_point_rate - 10)),
        }
        focus = offense.tactics.offensive_focus
        if focus is OffensiveFocus.INSIDE:
            w[RIM] *= 1.2; w[CLOSE] *= 1.2; w[THREE] *= 0.85
        elif focus is OffensiveFocus.PERIMETER:
            w[RIM] *= 0.9; w[CLOSE] *= 0.9; w[THREE] *= 1.2
        if defense.tactics.defensive_scheme is DefensiveScheme.ZONE:
            w[THREE] *= 1.15; w[RIM] *= 0.9
        if fast_break:
            w[RIM] *= 4; w[MID] *= 0.5
        if putback:
            w[RIM] *= 3; w[CLOSE] *= 1.5; w[THREE] *= 0.3
        return [w[z] for z in ZONES]

    def _defender(self, offense: _Side, defense: _Side, slot: int) -> _InGame:
        scheme = defense.tactics.defensive_scheme
        if scheme is DefensiveScheme.SWITCH and self.rng.random() < 0.35:
            return self.rng.choice(defense.lineup)   # a switch leaves a mismatch
        return defense.lineup[slot]

    def _make_prob(self, ig: _InGame, zone: str, offense: _Side, defense: _Side,
                   defender: _InGame, fast_break: bool, putback: bool) -> float:
        t = self.t
        protector = max(defense.lineup, key=lambda d: d.r("interior_defense"))
        scheme = defense.tactics.defensive_scheme
        if zone == RIM:
            body = (ig.r("strength") + ig.r("vertical")) / 2
            rim_d = 0.5 * defender.r("interior_defense") + 0.5 * protector.r("interior_defense")
            p = (t.make[RIM] + 0.012 * (ig.r("finishing") - 10) + 0.004 * (body - 10)
                 - 0.009 * (rim_d - 10))
            if scheme is DefensiveScheme.ZONE:
                p -= 0.02
            if fast_break:
                p += 0.08
            if putback:
                p += 0.03
        elif zone == CLOSE:
            d = 0.5 * defender.r("interior_defense") + 0.5 * defender.r("perimeter_defense")
            p = t.make[CLOSE] + 0.012 * (ig.r("close_range") - 10) - 0.007 * (d - 10)
        elif zone == MID:
            p = t.make[MID] + 0.011 * (ig.r("mid_range") - 10) - 0.006 * (defender.r("perimeter_defense") - 10)
        else:
            p = (t.make[THREE] + 0.011 * (ig.r("three_point") - 10)
                 - 0.006 * (defender.r("perimeter_defense") - 10))
            if scheme is DefensiveScheme.ZONE:
                p += 0.01
        p += 0.002 * (ig.r("basketball_iq") - 10) + 0.002 * (ig.r("composure") - 10)
        if offense.home:
            p += t.home_make_bonus
        lead = offense.score - defense.score
        p -= _clamp(lead * t.score_effect, -t.score_effect_cap, t.score_effect_cap)
        if self.clutch_time():
            p += 0.004 * (ig.p.hidden.clutch - 10)
        if scheme is DefensiveScheme.PRESS:
            p += 0.01                      # pressing leaves the back line exposed
        return _clamp(p * ig.form, 0.03, 0.95)

    # -- one shot attempt -----------------------------------------------------
    def attempt(self, offense: _Side, defense: _Side, fast_break: bool,
                putback: Optional[_InGame] = None) -> str:
        """Returns 'score' (dead ball), 'miss' or 'miss_ft' (live rebound).

        ``putback`` is the offensive rebounder going straight back up.
        """
        rng, t = self.rng, self.t
        if putback is not None:
            slot = offense.lineup.index(putback)
        else:
            weights = [math.exp(USAGE * (_offense_skill(ig) - 10)) for ig in offense.lineup]
            slot = rng.choices(range(5), weights)[0]
        shooter = offense.lineup[slot]
        defender = self._defender(offense, defense, slot)
        zone = _pick(rng, ZONES, self._zone_weights(shooter, offense, defense, fast_break,
                                                     putback is not None))
        points = 3 if zone == THREE else 2

        # Block?
        if zone in (RIM, CLOSE) and rng.random() < 0.5:
            blocker = max(defense.lineup, key=lambda d: d.r("blocking"))
        else:
            blocker = defender
        p_block = t.block[zone] * math.exp(0.15 * (blocker.r("blocking") - 10)
                                           - 0.02 * (shooter.p.physique.height_cm - blocker.p.physique.height_cm) / 2.54)
        if rng.random() < p_block:
            shooter.line.fga += 1
            shooter.line.tpa += zone == THREE
            blocker.line.blk += 1
            self.log(offense, f"{shooter.p.name} {ZONE_TEXT[zone]} blocked by {blocker.p.name}")
            return "miss"

        make_p = self._make_prob(shooter, zone, offense, defense, defender, fast_break,
                                 putback is not None)

        # Shooting foul?
        p_foul = t.shooting_foul[zone] * math.exp(0.05 * (shooter.r("strength") - 10)
                                                  - 0.04 * (defender.r("basketball_iq") - 10))
        if rng.random() < p_foul:
            fouler = defender
            if zone == RIM and rng.random() < 0.4:
                fouler = max(defense.lineup, key=lambda d: d.r("interior_defense"))
            self.foul(defense, fouler)
            if rng.random() < make_p * t.and_one_make_factor:
                shooter.line.fgm += 1
                shooter.line.fga += 1
                if zone == THREE:
                    shooter.line.tpm += 1
                    shooter.line.tpa += 1
                self.score(offense, points)
                self._credit_assist(offense, shooter, zone, fast_break, putback is not None)
                self.log(offense, f"{shooter.p.name} {ZONE_TEXT[zone]} AND ONE (foul {fouler.p.name})")
                n = 1
            else:
                self.log(offense, f"Shooting foul on {fouler.p.name}")
                n = points
            last_missed = self.free_throws(offense, shooter, n)
            if not last_missed:            # dead ball after a made final free throw
                self.substitutions(offense)
                self.substitutions(defense)
            return "miss_ft" if last_missed else "score"

        shooter.line.fga += 1
        if zone == THREE:
            shooter.line.tpa += 1
        if rng.random() < make_p:
            shooter.line.fgm += 1
            if zone == THREE:
                shooter.line.tpm += 1
            self.score(offense, points)
            assister = self._credit_assist(offense, shooter, zone, fast_break, putback is not None)
            extra = f" (assist {assister.p.name})" if assister else ""
            self.log(offense, f"{shooter.p.name} makes {ZONE_TEXT[zone]}{extra}")
            return "score"
        self.log(offense, f"{shooter.p.name} misses {ZONE_TEXT[zone]}")
        return "miss"

    def _credit_assist(self, offense: _Side, shooter: _InGame, zone: str,
                       fast_break: bool, putback: bool) -> Optional[_InGame]:
        if putback:
            return None
        mates = [ig for ig in offense.lineup if ig is not shooter]
        passing = sum(ig.r("passing") for ig in mates) / len(mates)
        p = self.t.assist[zone] * math.exp(0.05 * (passing - 10)) * (0.75 if fast_break else 1.0)
        if self.rng.random() >= p:
            return None
        w = [math.exp(0.45 * ((ig.r("passing") + ig.r("vision")) / 2 - 10)) for ig in mates]
        assister = _pick(self.rng, mates, w)
        assister.line.ast += 1
        return assister

    # -- one possession -------------------------------------------------------
    def possession(self, offense: _Side, defense: _Side, transition: str) -> str:
        """Play one possession. ``transition`` is how the ball was won: 'dead', 'drb' or 'steal'.

        Returns how it ended: 'dead', 'drb' or 'steal'.
        """
        rng, t = self.rng, self.t
        offense.possessions += 1
        pace = 0.6 * offense.tactics.pace + 0.4 * defense.tactics.pace
        fb_base = {"steal": t.fast_break_after_steal, "drb": t.fast_break_after_rebound}.get(transition, 0)
        fb_base *= math.exp(0.05 * (pace - 10)
                            + 0.04 * (defense.tactics.crash_offensive_glass - 10))
        fast_break = rng.random() < fb_base
        if fast_break:
            seconds = rng.uniform(3, 7)
        else:
            mean = t.seconds_per_possession * math.exp(-0.025 * (pace - 10))
            if defense.tactics.defensive_scheme is DefensiveScheme.PRESS:
                mean *= 0.92
            seconds = _clamp(rng.gauss(mean, t.possession_sd), 3, self.rules.shot_clock_seconds - 1)
        self.advance(seconds)

        # Turnover?
        handler_w = [math.exp(0.75 * USAGE * (_offense_skill(ig) - 10) + 0.15 * (ig.r("ball_handling") - 10))
                     for ig in offense.lineup]
        handler = _pick(rng, offense.lineup, handler_w)
        security = 0.5 * handler.r("ball_handling") + 0.3 * handler.r("passing") + 0.2 * handler.r("basketball_iq")
        hands = sum(d.r("stealing") for d in defense.lineup) / 5
        p_to = t.turnover_rate + 0.007 * (hands - 10) - 0.008 * (security - 10)
        scheme = defense.tactics.defensive_scheme
        if scheme is DefensiveScheme.PRESS:
            p_to += 0.025
        elif scheme is DefensiveScheme.ZONE:
            p_to -= 0.01
        if fast_break:
            p_to *= 0.6
        if rng.random() < _clamp(p_to, 0.04, 0.3):
            handler.line.tov += 1
            if rng.random() < t.steal_share:
                thief = _pick(rng, defense.lineup,
                              [math.exp(0.35 * (d.r("stealing") - 10)) for d in defense.lineup])
                thief.line.stl += 1
                self.log(offense, f"{handler.p.name} turnover, stolen by {thief.p.name}")
                return "steal"
            if rng.random() < t.offensive_foul_share:
                handler.line.pf += 1       # offensive fouls don't count toward team fouls
                self.log(offense, f"Offensive foul on {handler.p.name}")
                if handler.fouled_out:
                    self.substitutions(offense)
            else:
                self.log(offense, f"{handler.p.name} turnover")
            return "dead"

        # Non-shooting foul (reach-in, holding...)
        if not fast_break and rng.random() < t.non_shooting_foul_rate:
            fouler = _pick(rng, defense.lineup,
                           [math.exp(-0.08 * (d.r("basketball_iq") - 10)) for d in defense.lineup])
            self.foul(defense, fouler)
            bonus = defense.team_fouls >= self.rules.team_fouls_bonus
            self.log(defense, f"Foul on {fouler.p.name}" + (" (bonus)" if bonus else ""))
            self.substitutions(offense)
            self.substitutions(defense)
            if bonus:
                shooter = _pick(rng, offense.lineup, handler_w)
                result = "miss_ft" if self.free_throws(offense, shooter, 2) else "score"
                return self._finish(offense, defense, result)
            self.advance(rng.uniform(2, 6))   # inbound and reset
            if self.clock <= 0:
                return "dead"

        result = self.attempt(offense, defense, fast_break)
        return self._finish(offense, defense, result)

    def _finish(self, offense: _Side, defense: _Side, result: str) -> str:
        """Keep playing through offensive rebounds until the possession ends."""
        while result in ("miss", "miss_ft"):
            if self.clock <= 0:
                return "dead"
            rebounder = self.rebound(offense, defense, after_ft=result == "miss_ft")
            if rebounder is None:
                return "drb"
            # Tip-ins and putbacks go straight up; otherwise the ball is kicked out and reset.
            if self.rng.random() < 0.45:
                self.advance(self.rng.uniform(1, 3))
                result = self.attempt(offense, defense, fast_break=False, putback=rebounder)
            else:
                self.advance(self.rng.uniform(4, 10))
                result = self.attempt(offense, defense, fast_break=False)
        return "dead"

    # -- periods and the game -------------------------------------------------
    def jump_ball(self) -> _Side:
        hc, ac = self.home.lineup[4], self.away.lineup[4]
        edge = 0.02 * (hc.r("vertical") - ac.r("vertical")) + 0.01 * (hc.p.physique.height_cm - ac.p.physique.height_cm)
        return self.home if self.rng.random() < 0.5 + _clamp(edge, -0.3, 0.3) else self.away

    def play_period(self, starting: _Side) -> None:
        self.period += 1
        minutes = self.rules.quarter_minutes if self.period <= 4 else self.rules.overtime_minutes
        self.clock = minutes * 60.0
        for side in (self.home, self.away):
            side.team_fouls = 0
            side.period_points.append(0)
            if self.period > 1:
                recovery = self.t.halftime_recovery if self.period == 3 else self.t.period_break_recovery
                for ig in side.players.values():
                    ig.energy = min(1.0, ig.energy + recovery)
                self.substitutions(side)
        self.log(None, f"Start of {'Q' + str(self.period) if self.period <= 4 else 'OT' + str(self.period - 4)}")
        if self.period == 1 or self.period > 4:
            self.log(starting, f"{starting.team.abbreviation} win the tip")
        offense, ending = starting, "dead"
        while self.clock > 0:
            ending = self.possession(offense, self.other(offense), ending)
            offense = self.other(offense)
            if ending == "dead" and self.clock > 0:
                self.substitutions(self.home)
                self.substitutions(self.away)

    def run(self) -> GameSim:
        tip = self.jump_ball()
        other = self.other(tip)
        for starting in (tip, other, other, tip):
            self.play_period(starting)
        while self.home.score == self.away.score:
            self.play_period(self.jump_ball())
        self.log(None, "Final")
        boxes = []
        for side in (self.home, self.away):
            lines = sorted((ig.line for ig in side.players.values()),
                           key=lambda l: (not l.starter, -l.seconds))
            boxes.append(TeamBox(side.team.id, side.period_points, lines, side.possessions))
        result = GameResult(self.home.score, self.away.score, overtimes=max(0, self.period - 4))
        return GameSim(result, BoxScore(*boxes), self.events)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def simulate_game(league: League, game: Game, rng: Optional[random.Random] = None, *,
                  record_pbp: bool = False, tuning: Tuning = DEFAULT_TUNING) -> GameSim:
    """Simulate ``game`` without changing the league."""
    return _GameSim(league, game, rng or random.Random(), tuning, record_pbp).run()


def play_game(league: League, game_id: int, rng: Optional[random.Random] = None, *,
              record_pbp: bool = False, tuning: Tuning = DEFAULT_TUNING) -> GameSim:
    """Simulate a scheduled game and store its result and box score."""
    game = league.games[game_id]
    if game.played:
        raise ValueError(f"game {game_id} has already been played")
    sim = simulate_game(league, game, rng, record_pbp=record_pbp, tuning=tuning)
    game.result, game.box_score = sim.result, sim.box_score
    return sim


def play_day(league: League, day: int, rng: Optional[random.Random] = None) -> list[Game]:
    games = sorted((g for g in league.games.values()
                    if g.season == league.season and g.day == day and not g.played),
                   key=lambda g: g.id)
    for g in games:
        play_game(league, g.id, rng)
    return games


def play_season(league: League, rng: Optional[random.Random] = None) -> None:
    """Play every unplayed regular-season game, day by day."""
    days = sorted({g.day for g in league.games.values()
                   if g.season == league.season and not g.playoff and not g.played})
    for day in days:
        play_day(league, day, rng)
