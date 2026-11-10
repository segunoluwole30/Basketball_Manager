"""Core data model for the basketball league simulator.

Design notes
------------
* Attributes use a 1-20 scale. 10 is a league-average professional.
* ``current_ability`` (CA) is derived from attributes, weighted by position,
  on a 1-200 scale. ``potential_ability`` (PA) is a hidden ceiling, also 1-200.
* The League object owns every Team, Player and Game. A team's roster is
  derived from ``player.team_id`` so there is one source of truth.
* Every class round-trips through ``to_dict`` / ``from_dict`` so it can be
  stored as JSON (see ``hoopsim.db``).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import date
from enum import Enum
from typing import Optional

ATTR_MIN, ATTR_MAX = 1, 20
ABILITY_MIN, ABILITY_MAX = 1, 200


def clamp(value: float, lo: int = ATTR_MIN, hi: int = ATTR_MAX) -> int:
    return int(max(lo, min(hi, round(value))))


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------
class Position(str, Enum):
    PG = "PG"
    SG = "SG"
    SF = "SF"
    PF = "PF"
    C = "C"


class DefensiveScheme(str, Enum):
    MAN = "man"
    SWITCH = "switch"
    ZONE = "zone"
    PRESS = "press"


class OffensiveFocus(str, Enum):
    INSIDE = "inside"
    BALANCED = "balanced"
    PERIMETER = "perimeter"


class ContractOption(str, Enum):
    PLAYER = "player"
    TEAM = "team"


# --------------------------------------------------------------------------
# Player components
# --------------------------------------------------------------------------
@dataclass
class _AttributeBlock:
    """Base for blocks of 1-20 ratings: validation + helpers."""

    def __post_init__(self) -> None:
        for f in fields(self):
            v = getattr(self, f.name)
            if not isinstance(v, int) or not ATTR_MIN <= v <= ATTR_MAX:
                raise ValueError(f"{f.name}={v!r} must be an int in {ATTR_MIN}-{ATTR_MAX}")

    @classmethod
    def names(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict):
        return cls(**d)


@dataclass
class Attributes(_AttributeBlock):
    """Visible ratings that the match engine reads."""

    # Scoring
    finishing: int = 10          # layups and dunks at the rim
    close_range: int = 10        # post moves, floaters, short hooks
    mid_range: int = 10
    three_point: int = 10
    free_throw: int = 10
    # Playmaking
    passing: int = 10
    ball_handling: int = 10
    vision: int = 10
    # Defence
    perimeter_defense: int = 10
    interior_defense: int = 10
    stealing: int = 10
    blocking: int = 10
    # Rebounding
    offensive_rebounding: int = 10
    defensive_rebounding: int = 10
    # Physical
    speed: int = 10
    strength: int = 10
    vertical: int = 10
    stamina: int = 10
    # Mental
    basketball_iq: int = 10
    composure: int = 10


@dataclass
class Tendencies(_AttributeBlock):
    """How a player likes to play, 1-20 (10 = no preference). Mostly follows his
    skills but not always: some players love a shot they aren't great at. Managers
    can change them with player instructions."""

    three_point: int = 10        # looks for his three
    mid_range: int = 10          # pull-ups and elbow jumpers
    drive: int = 10              # attacks the basket off the dribble
    post_up: int = 10            # calls for it on the block
    passing: int = 10            # pass-first
    cutting: int = 10            # moves without the ball

    @classmethod
    def from_attributes(cls, a: "Attributes", noise=None) -> "Tendencies":
        """Skill-based tendencies; ``noise()`` (if given) adds personal quirks."""
        n = noise or (lambda: 0.0)
        raw = {
            "three_point": 10 + 0.7 * (a.three_point - 10),
            "mid_range": 10 + 0.7 * (a.mid_range - 10),
            "drive": 10 + 0.4 * (a.ball_handling - 10) + 0.3 * (a.speed - 10) + 0.2 * (a.finishing - 10),
            "post_up": 10 + 0.4 * (a.close_range - 10) + 0.4 * (a.strength - 10),
            "passing": 10 + 0.4 * (a.passing - 10) + 0.4 * (a.vision - 10),
            "cutting": 10 + 0.4 * (a.basketball_iq - 10) + 0.3 * (a.speed - 10) - 0.3 * (a.three_point - 10),
        }
        # +1: generated ratings average a little under 10; keep the league's average style neutral.
        return cls(**{k: max(ATTR_MIN, min(ATTR_MAX, round(v + 1 + n()))) for k, v in raw.items()})


@dataclass
class HiddenAttributes(_AttributeBlock):
    """Ratings the user never sees directly (scouting reveals hints)."""

    consistency: int = 10        # variance of game-to-game performance
    clutch: int = 10             # performance in close late-game situations
    injury_proneness: int = 10   # higher = more injuries
    work_ethic: int = 10         # drives development speed
    professionalism: int = 10    # slows decline, steadies morale
    ambition: int = 10           # wants to win / bigger contracts
    loyalty: int = 10            # willingness to re-sign


# How much each attribute matters to CA for each position (weights per row
# are relative; they're normalised when used).
_W = {
    #                  fin cls mid 3pt  ft pas  bh vis per int stl blk orb drb spd str vrt sta  iq cmp
    Position.PG: (2, 1, 2, 3, 1, 4, 4, 4, 3, 1, 2, 0.5, 0.5, 1, 3, 1, 1, 2, 3, 2),
    Position.SG: (3, 1, 3, 4, 1, 2, 3, 2, 3, 1, 2, 0.5, 0.5, 1, 3, 1, 2, 2, 2, 2),
    Position.SF: (3, 2, 2, 3, 1, 2, 2, 2, 3, 2, 2, 1, 1, 2, 2, 2, 2, 2, 2, 2),
    Position.PF: (3, 3, 2, 1.5, 1, 1.5, 1, 1.5, 2, 3, 1, 2, 2, 3, 1.5, 3, 2, 2, 2, 2),
    Position.C:  (4, 3, 1, 0.5, 1, 1.5, 0.5, 1.5, 1, 4, 1, 4, 3, 4, 1, 4, 2, 2, 2, 2),
}
POSITION_WEIGHTS: dict[Position, dict[str, float]] = {
    pos: dict(zip(Attributes.names(), w, strict=True)) for pos, w in _W.items()
}


def compute_ability(attrs: Attributes, position: Position) -> int:
    """Position-weighted attribute average mapped from 1-20 onto 1-200."""
    weights = POSITION_WEIGHTS[position]
    total = sum(weights.values())
    mean = sum(getattr(attrs, n) * w for n, w in weights.items()) / total
    return clamp((mean - ATTR_MIN) / (ATTR_MAX - ATTR_MIN) * (ABILITY_MAX - ABILITY_MIN) + ABILITY_MIN,
                 ABILITY_MIN, ABILITY_MAX)


@dataclass
class Physique:
    height_cm: int
    wingspan_cm: int
    weight_kg: int

    def __post_init__(self) -> None:
        if not 160 <= self.height_cm <= 235:
            raise ValueError(f"height_cm={self.height_cm} out of range")
        if not 150 <= self.wingspan_cm <= 250:
            raise ValueError(f"wingspan_cm={self.wingspan_cm} out of range")
        if not 60 <= self.weight_kg <= 160:
            raise ValueError(f"weight_kg={self.weight_kg} out of range")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Physique":
        return cls(**d)


@dataclass
class Contract:
    salary: int                    # per season, in league currency units
    expires_season: int            # last season covered (inclusive)
    option: Optional[ContractOption] = None   # option on the final season
    rookie_scale: bool = False

    def __post_init__(self) -> None:
        if self.salary < 0:
            raise ValueError("salary must be non-negative")

    def years_remaining(self, season: int) -> int:
        return max(0, self.expires_season - season + 1)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["option"] = self.option.value if self.option else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Contract":
        return cls(**{**d, "option": ContractOption(d["option"]) if d.get("option") else None})


@dataclass
class Injury:
    description: str
    games_remaining: int

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Injury":
        return cls(**d)


@dataclass
class Player:
    id: int
    first_name: str
    last_name: str
    birth_date: date
    nationality: str
    position: Position
    physique: Physique
    attributes: Attributes
    hidden: HiddenAttributes
    potential_ability: int
    secondary_positions: list[Position] = field(default_factory=list)
    team_id: Optional[int] = None
    contract: Optional[Contract] = None
    jersey_number: Optional[int] = None
    morale: float = 50.0           # 0-100
    injury: Optional[Injury] = None
    retired: bool = False
    draft_year: Optional[int] = None   # season of the draft that brought him in
    draft_pick: Optional[int] = None   # overall pick; None = undrafted
    career: list[dict] = field(default_factory=list)  # one stat line per season (see stats.py)
    tendencies: Optional[Tendencies] = None             # derived from attributes if not given

    def __post_init__(self) -> None:
        if self.tendencies is None:
            self.tendencies = Tendencies.from_attributes(self.attributes)
        if not ABILITY_MIN <= self.potential_ability <= ABILITY_MAX:
            raise ValueError("potential_ability out of range")
        if not 0 <= self.morale <= 100:
            raise ValueError("morale must be 0-100")

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def age(self, on: date) -> int:
        years = on.year - self.birth_date.year
        if (on.month, on.day) < (self.birth_date.month, self.birth_date.day):
            years -= 1
        return years

    @property
    def current_ability(self) -> int:
        return compute_ability(self.attributes, self.position)

    def familiarity(self, position: Position) -> float:
        """1.0 at natural position, less when playing out of position."""
        if position == self.position:
            return 1.0
        if position in self.secondary_positions:
            return 0.95
        gap = abs(list(Position).index(position) - list(Position).index(self.position))
        return 0.88 if gap == 1 else 0.75

    def ability_at(self, position: Position) -> int:
        """Effective ability when played at ``position``, including familiarity."""
        return round(compute_ability(self.attributes, position) * self.familiarity(position))

    @property
    def is_available(self) -> bool:
        return self.injury is None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "birth_date": self.birth_date.isoformat(),
            "nationality": self.nationality,
            "position": self.position.value,
            "secondary_positions": [p.value for p in self.secondary_positions],
            "physique": self.physique.to_dict(),
            "attributes": self.attributes.to_dict(),
            "hidden": self.hidden.to_dict(),
            "potential_ability": self.potential_ability,
            "team_id": self.team_id,
            "contract": self.contract.to_dict() if self.contract else None,
            "jersey_number": self.jersey_number,
            "morale": self.morale,
            "injury": self.injury.to_dict() if self.injury else None,
            "retired": self.retired,
            "draft_year": self.draft_year,
            "draft_pick": self.draft_pick,
            "career": self.career,
            "tendencies": self.tendencies.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Player":
        return cls(
            id=d["id"],
            first_name=d["first_name"],
            last_name=d["last_name"],
            birth_date=date.fromisoformat(d["birth_date"]),
            nationality=d["nationality"],
            position=Position(d["position"]),
            secondary_positions=[Position(p) for p in d.get("secondary_positions", [])],
            physique=Physique.from_dict(d["physique"]),
            attributes=Attributes.from_dict(d["attributes"]),
            hidden=HiddenAttributes.from_dict(d["hidden"]),
            potential_ability=d["potential_ability"],
            team_id=d.get("team_id"),
            contract=Contract.from_dict(d["contract"]) if d.get("contract") else None,
            jersey_number=d.get("jersey_number"),
            morale=d.get("morale", 50.0),
            injury=Injury.from_dict(d["injury"]) if d.get("injury") else None,
            retired=d.get("retired", False),
            draft_year=d.get("draft_year"),
            draft_pick=d.get("draft_pick"),
            career=list(d.get("career", [])),
            tendencies=Tendencies.from_dict(d["tendencies"]) if d.get("tendencies") else None,
        )


# --------------------------------------------------------------------------
# Team
# --------------------------------------------------------------------------
@dataclass
class Tactics:
    pace: int = 10                 # 1-20: possessions per game tendency
    three_point_rate: int = 10     # 1-20: willingness to shoot threes
    offensive_focus: OffensiveFocus = OffensiveFocus.BALANCED
    defensive_scheme: DefensiveScheme = DefensiveScheme.MAN
    crash_offensive_glass: int = 10   # 1-20: trade transition D for O-boards
    rotation_depth: int = 9        # how many players get regular minutes
    starters: list[int] = field(default_factory=list)          # 5 player ids
    minutes_targets: dict[int, int] = field(default_factory=dict)  # player id -> mpg

    def __post_init__(self) -> None:
        for name in ("pace", "three_point_rate", "crash_offensive_glass"):
            v = getattr(self, name)
            if not ATTR_MIN <= v <= ATTR_MAX:
                raise ValueError(f"{name} must be {ATTR_MIN}-{ATTR_MAX}")
        if not 5 <= self.rotation_depth <= 15:
            raise ValueError("rotation_depth must be 5-15")
        if len(self.starters) not in (0, 5) or len(set(self.starters)) != len(self.starters):
            raise ValueError("starters must be empty or 5 distinct player ids")

    def to_dict(self) -> dict:
        return {
            "pace": self.pace,
            "three_point_rate": self.three_point_rate,
            "offensive_focus": self.offensive_focus.value,
            "defensive_scheme": self.defensive_scheme.value,
            "crash_offensive_glass": self.crash_offensive_glass,
            "rotation_depth": self.rotation_depth,
            "starters": list(self.starters),
            # JSON object keys must be strings
            "minutes_targets": {str(k): v for k, v in self.minutes_targets.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Tactics":
        return cls(
            pace=d["pace"],
            three_point_rate=d["three_point_rate"],
            offensive_focus=OffensiveFocus(d["offensive_focus"]),
            defensive_scheme=DefensiveScheme(d["defensive_scheme"]),
            crash_offensive_glass=d["crash_offensive_glass"],
            rotation_depth=d["rotation_depth"],
            starters=list(d.get("starters", [])),
            minutes_targets={int(k): v for k, v in d.get("minutes_targets", {}).items()},
        )


@dataclass
class Team:
    id: int
    city: str
    name: str
    abbreviation: str
    conference: str
    division: str
    tactics: Tactics = field(default_factory=Tactics)
    reputation: int = 50           # 1-100: attracts free agents, sponsors
    budget: int = 0                # spending money in league currency
    arena_capacity: int = 18000
    auto_lineup: bool = True       # let the coach (AI) set starters and minutes

    @property
    def full_name(self) -> str:
        return f"{self.city} {self.name}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tactics"] = self.tactics.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Team":
        return cls(**{**d, "tactics": Tactics.from_dict(d["tactics"])})


# --------------------------------------------------------------------------
# Schedule
# --------------------------------------------------------------------------
@dataclass
class GameResult:
    home_score: int
    away_score: int
    overtimes: int = 0

    def __post_init__(self) -> None:
        if self.home_score == self.away_score:
            raise ValueError("basketball games can't end tied")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GameResult":
        return cls(**d)


STAT_FIELDS = ("fgm", "fga", "tpm", "tpa", "ftm", "fta", "orb", "drb",
               "ast", "stl", "blk", "tov", "pf", "plus_minus")


@dataclass
class PlayerLine:
    """One player's box-score line. ``fgm``/``fga`` include three-pointers."""

    player_id: int
    starter: bool = False
    seconds: float = 0.0
    fgm: int = 0
    fga: int = 0
    tpm: int = 0
    tpa: int = 0
    ftm: int = 0
    fta: int = 0
    orb: int = 0
    drb: int = 0
    ast: int = 0
    stl: int = 0
    blk: int = 0
    tov: int = 0
    pf: int = 0
    plus_minus: int = 0

    @property
    def pts(self) -> int:
        return 2 * self.fgm + self.tpm + self.ftm

    @property
    def trb(self) -> int:
        return self.orb + self.drb

    @property
    def minutes(self) -> float:
        return self.seconds / 60

    def to_dict(self) -> dict:
        d = asdict(self)
        d["seconds"] = round(self.seconds, 1)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PlayerLine":
        return cls(**d)


@dataclass
class TeamBox:
    team_id: int
    period_points: list[int] = field(default_factory=list)
    lines: list[PlayerLine] = field(default_factory=list)
    possessions: int = 0

    @property
    def points(self) -> int:
        return sum(self.period_points)

    def totals(self) -> PlayerLine:
        total = PlayerLine(player_id=0)
        for line in self.lines:
            total.seconds += line.seconds
            for f in STAT_FIELDS:
                setattr(total, f, getattr(total, f) + getattr(line, f))
        total.plus_minus = 0
        return total

    def to_dict(self) -> dict:
        return {"team_id": self.team_id, "period_points": list(self.period_points),
                "lines": [l.to_dict() for l in self.lines], "possessions": self.possessions}

    @classmethod
    def from_dict(cls, d: dict) -> "TeamBox":
        return cls(d["team_id"], list(d["period_points"]),
                   [PlayerLine.from_dict(l) for l in d["lines"]], d.get("possessions", 0))


@dataclass
class BoxScore:
    home: TeamBox
    away: TeamBox

    def to_dict(self) -> dict:
        return {"home": self.home.to_dict(), "away": self.away.to_dict()}

    @classmethod
    def from_dict(cls, d: dict) -> "BoxScore":
        return cls(TeamBox.from_dict(d["home"]), TeamBox.from_dict(d["away"]))


@dataclass
class Game:
    id: int
    season: int
    day: int                       # day of the season, 1-based
    home_team_id: int
    away_team_id: int
    playoff: bool = False
    result: Optional[GameResult] = None
    box_score: Optional[BoxScore] = None

    def __post_init__(self) -> None:
        if self.home_team_id == self.away_team_id:
            raise ValueError("a team can't play itself")

    @property
    def played(self) -> bool:
        return self.result is not None

    def winner_id(self) -> Optional[int]:
        if not self.result:
            return None
        return self.home_team_id if self.result.home_score > self.result.away_score else self.away_team_id

    def to_dict(self) -> dict:
        return {
            "id": self.id, "season": self.season, "day": self.day,
            "home_team_id": self.home_team_id, "away_team_id": self.away_team_id,
            "playoff": self.playoff,
            "result": self.result.to_dict() if self.result else None,
            "box_score": self.box_score.to_dict() if self.box_score else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Game":
        return cls(
            id=d["id"], season=d["season"], day=d["day"],
            home_team_id=d["home_team_id"], away_team_id=d["away_team_id"],
            playoff=d.get("playoff", False),
            result=GameResult.from_dict(d["result"]) if d.get("result") else None,
            box_score=BoxScore.from_dict(d["box_score"]) if d.get("box_score") else None,
        )


# --------------------------------------------------------------------------
# League
# --------------------------------------------------------------------------
@dataclass
class LeagueRules:
    salary_cap: int = 140_000_000
    luxury_tax_line: int = 170_000_000
    min_salary: int = 1_100_000
    max_salary_cap_fraction: float = 0.35
    min_roster: int = 13
    max_roster: int = 15
    games_per_season: int = 82
    quarter_minutes: int = 12
    overtime_minutes: int = 5
    shot_clock_seconds: int = 24
    personal_foul_limit: int = 6
    team_fouls_bonus: int = 5      # team fouls per quarter before free throws
    timeouts_per_game: int = 7
    timeouts_per_overtime: int = 2
    playoff_series_length: int = 7
    draft_rounds: int = 2
    mid_level_exception: int = 12_500_000   # one signing per offseason for teams over the cap

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LeagueRules":
        return cls(**d)


class RosterError(Exception):
    pass


class Phase(str, Enum):
    REGULAR_SEASON = "regular_season"
    PLAYOFFS = "playoffs"
    OFFSEASON = "offseason"


@dataclass
class Series:
    """A playoff series. The higher seed has home court."""

    id: int
    round: int                     # 1 = first round
    conference: Optional[str]      # None for the finals
    high_id: int
    low_id: int
    high_seed: int
    low_seed: int
    best_of: int = 7
    high_wins: int = 0
    low_wins: int = 0
    game_ids: list[int] = field(default_factory=list)

    @property
    def wins_needed(self) -> int:
        return self.best_of // 2 + 1

    @property
    def over(self) -> bool:
        return max(self.high_wins, self.low_wins) >= self.wins_needed

    @property
    def winner_id(self) -> Optional[int]:
        if not self.over:
            return None
        return self.high_id if self.high_wins > self.low_wins else self.low_id

    @property
    def loser_id(self) -> Optional[int]:
        w = self.winner_id
        return None if w is None else (self.low_id if w == self.high_id else self.high_id)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Series":
        return cls(**d)


@dataclass
class League:
    name: str
    season: int                    # e.g. 2026 for the 2026-27 season
    current_date: date
    rules: LeagueRules = field(default_factory=LeagueRules)
    teams: dict[int, Team] = field(default_factory=dict)
    players: dict[int, Player] = field(default_factory=dict)
    games: dict[int, Game] = field(default_factory=dict)
    phase: Phase = Phase.REGULAR_SEASON
    playoffs: list[Series] = field(default_factory=list)   # current season's bracket
    history: list[dict] = field(default_factory=list)      # one summary per finished season
    user_team_id: Optional[int] = None                     # the team you manage
    mle_used: list[int] = field(default_factory=list)       # teams that used the MLE this league year
    offseason: Optional[dict] = None                        # an offseason in progress (season.py)
    last_offseason: Optional[dict] = None                   # the last finished offseason's report
    inbox: list[dict] = field(default_factory=list)         # news for the user (web interface)

    # -- id allocation ------------------------------------------------------
    def next_player_id(self) -> int:
        return max(self.players, default=0) + 1

    def next_game_id(self) -> int:
        return max(self.games, default=0) + 1

    # -- queries ------------------------------------------------------------
    def roster(self, team_id: int) -> list[Player]:
        return sorted((p for p in self.players.values() if p.team_id == team_id),
                      key=lambda p: p.current_ability, reverse=True)

    def free_agents(self) -> list[Player]:
        return sorted((p for p in self.players.values() if p.team_id is None and not p.retired),
                      key=lambda p: p.current_ability, reverse=True)

    def payroll(self, team_id: int) -> int:
        return sum(p.contract.salary for p in self.roster(team_id) if p.contract)

    def cap_space(self, team_id: int) -> int:
        return self.rules.salary_cap - self.payroll(team_id)

    def standings(self) -> list[tuple[Team, int, int]]:
        """(team, wins, losses), best record first. Regular season only."""
        record = {tid: [0, 0] for tid in self.teams}
        for g in self.games.values():
            if g.season != self.season or g.playoff or not g.played:
                continue
            w = g.winner_id()
            loser = g.away_team_id if w == g.home_team_id else g.home_team_id
            record[w][0] += 1
            record[loser][1] += 1
        rows = [(self.teams[t], w, l) for t, (w, l) in record.items()]
        return sorted(rows, key=lambda r: (r[1] / max(1, r[1] + r[2]), r[1]), reverse=True)

    # -- mutations ----------------------------------------------------------
    def add_team(self, team: Team) -> None:
        if team.id in self.teams:
            raise ValueError(f"duplicate team id {team.id}")
        self.teams[team.id] = team

    def add_player(self, player: Player) -> None:
        if player.id in self.players:
            raise ValueError(f"duplicate player id {player.id}")
        if player.team_id is not None and player.team_id not in self.teams:
            raise ValueError(f"unknown team id {player.team_id}")
        self.players[player.id] = player

    def add_game(self, game: Game) -> None:
        if game.id in self.games:
            raise ValueError(f"duplicate game id {game.id}")
        for tid in (game.home_team_id, game.away_team_id):
            if tid not in self.teams:
                raise ValueError(f"unknown team id {tid}")
        self.games[game.id] = game

    def sign(self, player_id: int, team_id: int, contract: Contract) -> None:
        player = self.players[player_id]
        if player.team_id is not None:
            raise RosterError(f"{player.name} is under contract with team {player.team_id}")
        if team_id not in self.teams:
            raise ValueError(f"unknown team id {team_id}")
        if len(self.roster(team_id)) >= self.rules.max_roster:
            raise RosterError("roster is full")
        if contract.salary < self.rules.min_salary:
            raise RosterError("salary below league minimum")
        if contract.salary > self.rules.salary_cap * self.rules.max_salary_cap_fraction:
            raise RosterError("salary above league maximum")
        if contract.expires_season < self.season:
            raise RosterError("contract already expired")
        player.team_id = team_id
        player.contract = contract

    def release(self, player_id: int) -> None:
        player = self.players[player_id]
        if player.team_id is None:
            raise RosterError(f"{player.name} is already a free agent")
        tactics = self.teams[player.team_id].tactics
        if player_id in tactics.starters:
            tactics.starters = []
        tactics.minutes_targets.pop(player_id, None)
        player.team_id = None
        player.contract = None

    def trade(self, team_a: int, players_a: list[int], team_b: int, players_b: list[int]) -> None:
        """Swap players between two teams after checking roster limits."""
        for pid in players_a:
            if self.players[pid].team_id != team_a:
                raise RosterError(f"player {pid} is not on team {team_a}")
        for pid in players_b:
            if self.players[pid].team_id != team_b:
                raise RosterError(f"player {pid} is not on team {team_b}")
        size_a = len(self.roster(team_a)) - len(players_a) + len(players_b)
        size_b = len(self.roster(team_b)) - len(players_b) + len(players_a)
        if max(size_a, size_b) > self.rules.max_roster:
            raise RosterError("trade would exceed max roster size")
        for tid, outgoing in ((team_a, players_a), (team_b, players_b)):
            tactics = self.teams[tid].tactics
            if set(outgoing) & set(tactics.starters):
                tactics.starters = []
            for pid in outgoing:
                tactics.minutes_targets.pop(pid, None)
        for pid in players_a:
            self.players[pid].team_id = team_b
        for pid in players_b:
            self.players[pid].team_id = team_a
        # A new arrival whose number is taken picks the lowest free one.
        for tid, incoming in ((team_b, players_a), (team_a, players_b)):
            for pid in incoming:
                p = self.players[pid]
                taken = {q.jersey_number for q in self.roster(tid) if q.id != pid}
                if p.jersey_number in taken:
                    p.jersey_number = min(n for n in range(100) if n not in taken)

    # -- validation ---------------------------------------------------------
    def validate(self) -> list[str]:
        """Return a list of rule problems (empty means the league is legal)."""
        problems = []
        for team in self.teams.values():
            roster = self.roster(team.id)
            ids = {p.id for p in roster}
            if not self.rules.min_roster <= len(roster) <= self.rules.max_roster:
                problems.append(f"{team.abbreviation}: roster size {len(roster)}")
            if team.tactics.starters and not set(team.tactics.starters) <= ids:
                problems.append(f"{team.abbreviation}: starter not on roster")
            numbers = [p.jersey_number for p in roster if p.jersey_number is not None]
            if len(numbers) != len(set(numbers)):
                problems.append(f"{team.abbreviation}: duplicate jersey numbers")
            for p in roster:
                if p.contract is None:
                    problems.append(f"{team.abbreviation}: {p.name} has no contract")
        return problems
