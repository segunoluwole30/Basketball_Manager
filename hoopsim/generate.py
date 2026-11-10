"""Random league generation.

Players are built from three ingredients:
1. a position template (centres rebound, point guards pass...),
2. height relative to the position average (taller = better boards and
   blocks, worse handle and speed),
3. a quality level chosen so the player's current ability hits a target,
   which comes from potential ability and an age-based development curve.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Optional

from .lineup import POSITIONS, auto_lineup
from .models import (
    ABILITY_MAX, ABILITY_MIN, ATTR_MAX, Attributes, Contract, ContractOption, Game,
    HiddenAttributes, League, LeagueRules, Physique, Player, Position, Team, Tactics,
    clamp, compute_ability,
)


# Offsets from a league-average 10, in Attributes field order:
#            fin cls mid 3pt ft pas bh vis per int stl blk orb drb spd str vrt sta iq cmp
_TEMPLATES = {
    Position.PG: (0, -1, 1, 1, 1, 3, 3, 3, 1, -3, 2, -4, -4, -3, 3, -3, 0, 1, 1, 0),
    Position.SG: (1, -1, 2, 2, 1, 0, 1, 0, 1, -2, 1, -3, -3, -2, 2, -2, 1, 1, 0, 0),
    Position.SF: (1, 0, 1, 1, 0, 0, 0, 0, 1, 0, 1, -1, -1, 0, 1, 0, 1, 1, 0, 0),
    Position.PF: (1, 2, 0, -1, -1, -1, -2, -1, -1, 2, -1, 1, 2, 2, -1, 2, 0, 0, 0, 0),
    Position.C:  (2, 2, -2, -4, -2, -2, -4, -2, -3, 3, -2, 3, 3, 3, -3, 3, 0, -1, 0, 0),
}
TEMPLATES = {pos: dict(zip(Attributes.names(), t, strict=True)) for pos, t in _TEMPLATES.items()}

# Mean height (cm) and weight (kg) by position.
_BODY = {
    Position.PG: (188, 86), Position.SG: (196, 93), Position.SF: (203, 101),
    Position.PF: (207, 109), Position.C: (212, 116),
}
_HEIGHT_SD = 4.0
_TALL_PLUS = ("offensive_rebounding", "defensive_rebounding", "blocking", "interior_defense")
_TALL_MINUS = ("ball_handling", "speed")
PHYSICAL = ("speed", "vertical", "stamina")

# Fraction of potential reached at each age; interpolated in between.
_DEVELOPMENT = [(18, 0.58), (19, 0.62), (21, 0.72), (23, 0.83), (25, 0.93),
                (27, 1.0), (30, 0.99), (32, 0.95), (34, 0.88), (36, 0.80), (40, 0.65)]


def development_fraction(age: int) -> float:
    if age <= _DEVELOPMENT[0][0]:
        return _DEVELOPMENT[0][1]
    for (a0, f0), (a1, f1) in zip(_DEVELOPMENT, _DEVELOPMENT[1:]):
        if age <= a1:
            return f0 + (f1 - f0) * (age - a0) / (a1 - a0)
    return _DEVELOPMENT[-1][1]


FIRST_NAMES = [
    "Marcus", "Jalen", "Andre", "Tyrese", "Darius", "Elijah", "Malik", "Isaiah", "Caleb",
    "Jordan", "Devin", "Trey", "Kofi", "Tunde", "Luka", "Nikos", "Mateo", "Rafael", "Theo",
    "Jonas", "Emeka", "Ibrahim", "Kai", "Owen", "Felix", "Hugo", "Sami", "Leon", "Bruno",
    "Dante", "Xavier", "Quinn", "Reggie", "Victor", "Omari", "Chidi", "Sergio", "Aleksa",
]
LAST_NAMES = [
    "Okafor", "Williams", "Johnson", "Carter", "Brooks", "Mensah", "Petrovic", "Silva",
    "Moreau", "Hughes", "Bennett", "Adeyemi", "Kowalski", "Lindqvist", "Ramos", "Fischer",
    "Nakamura", "Diallo", "Ellis", "Grant", "Hayes", "Ibekwe", "Jovanovic", "Keller",
    "Lawson", "Mitchell", "Novak", "Osei", "Parker", "Quarles", "Reed", "Santos", "Thompson",
    "Udoh", "Vasquez", "Walker", "Young", "Zeller", "Bamba", "Costa", "Dimitrov", "Eze",
]
NATIONALITIES = [("USA", 70), ("Nigeria", 5), ("Serbia", 4), ("France", 4), ("Canada", 4),
                 ("Spain", 3), ("Germany", 3), ("Australia", 3), ("Brazil", 2), ("Greece", 2)]

TEAM_NAMES = [
    ("Harbor City", "Mariners", "HCM"), ("Redwood", "Lumberjacks", "RWL"),
    ("Iron Falls", "Forge", "IFF"), ("Sun Valley", "Scorchers", "SVS"),
    ("Northgate", "Wolves", "NGW"), ("Crescent Bay", "Tide", "CBT"),
    ("Granite Peak", "Summit", "GPS"), ("Lakeshore", "Herons", "LSH"),
    ("Silver Plains", "Stallions", "SPS"), ("Eastport", "Anchors", "EPA"),
    ("Copper Ridge", "Miners", "CRM"), ("Bluewater", "Sharks", "BWS"),
    ("Pine Hollow", "Owls", "PHO"), ("Stonebridge", "Titans", "SBT"),
    ("Westfield", "Comets", "WFC"), ("Maple Grove", "Monarchs", "MGM"),
    ("Thunder Bay", "Storm", "TBS"), ("Ashford", "Phoenix", "AFP"),
    ("Riverbend", "Rapids", "RBR"), ("Highland", "Hawks", "HLH"),
    ("Coral Springs", "Barracudas", "CSB"), ("Kingsport", "Royals", "KPR"),
    ("Desert Rock", "Vipers", "DRV"), ("Fairhaven", "Knights", "FHK"),
    ("Oakmont", "Bison", "OMB"), ("Brightwater", "Bolts", "BWB"),
    ("Cedar Point", "Coyotes", "CPC"), ("Glenwood", "Grizzlies", "GWG"),
    ("Starlight", "Rockets", "SLR"), ("Port Haven", "Pirates", "PHP"),
]


# --------------------------------------------------------------------------
# Players
# --------------------------------------------------------------------------
def _attributes_for_target(position: Position, height_z: float, age: int,
                           target_ca: int, rng: random.Random) -> Attributes:
    """Pick a level shift so the resulting attributes land on ``target_ca``."""
    template = TEMPLATES[position]
    noise = {n: rng.gauss(0, 1.8) for n in Attributes.names()}
    decline = max(0.0, (age - 30) * 0.6)

    def build(shift: float) -> Attributes:
        values = {}
        for n in Attributes.names():
            v = 10 + template[n] + shift + noise[n]
            if n in _TALL_PLUS:
                v += height_z
            elif n in _TALL_MINUS:
                v -= height_z
            if n in PHYSICAL:
                v -= decline
            values[n] = clamp(v)
        return Attributes(**values)

    lo, hi = -15.0, 15.0
    for _ in range(30):  # binary search; ability rises monotonically with shift
        mid = (lo + hi) / 2
        if compute_ability(build(mid), position) < target_ca:
            lo = mid
        else:
            hi = mid
    return build(hi)


def _hidden(rng: random.Random) -> HiddenAttributes:
    return HiddenAttributes(**{n: clamp(rng.gauss(10, 3.5)) for n in HiddenAttributes.names()})


def _secondary_positions(position: Position, rng: random.Random) -> list[Position]:
    i = POSITIONS.index(position)
    neighbours = [POSITIONS[j] for j in (i - 1, i + 1) if 0 <= j < len(POSITIONS)]
    return [p for p in neighbours if rng.random() < 0.5]


def birth_date_for_age(age: int, on: date, rng: random.Random) -> date:
    """A random birthday such that the player is exactly ``age`` on ``on``."""
    try:
        latest = on.replace(year=on.year - age)   # birthday today
    except ValueError:  # Feb 29 in a non-leap year
        latest = on.replace(year=on.year - age, day=28)
    # Born up to 364 days before that still gives the same age.
    return latest - timedelta(days=rng.randrange(365))


def generate_player(rng: random.Random, player_id: int, on: date, *,
                    position: Optional[Position] = None, age: Optional[int] = None,
                    potential: Optional[int] = None) -> Player:
    position = position or rng.choice(POSITIONS)
    age = age if age is not None else int(clamp(rng.gauss(26, 4), 19, 37))
    potential = potential if potential is not None else int(clamp(rng.gauss(105, 25), 45, 195))
    if not ABILITY_MIN <= potential <= ABILITY_MAX:
        raise ValueError("potential out of range")

    mean_h, mean_w = _BODY[position]
    height = int(clamp(rng.gauss(mean_h, _HEIGHT_SD), 170, 230))
    height_z = (height - mean_h) / _HEIGHT_SD
    wingspan = int(clamp(height + rng.gauss(6, 5), 160, 245))
    weight = int(clamp(mean_w + (height - mean_h) * 0.9 + rng.gauss(0, 5), 65, 150))

    # Spread around the curve so not every player of an age is equally developed.
    frac = min(1.0, development_fraction(age) * rng.uniform(0.92, 1.05))
    target = max(ABILITY_MIN, round(potential * frac))
    attrs = _attributes_for_target(position, height_z, age, target, rng)

    nations, weights = zip(*NATIONALITIES)
    return Player(
        id=player_id,
        first_name=rng.choice(FIRST_NAMES),
        last_name=rng.choice(LAST_NAMES),
        birth_date=birth_date_for_age(age, on, rng),
        nationality=rng.choices(nations, weights)[0],
        position=position,
        secondary_positions=_secondary_positions(position, rng),
        physique=Physique(height, wingspan, weight),
        attributes=attrs,
        hidden=_hidden(rng),
        # Ability can exceed the drawn potential by rounding; PA must stay >= CA.
        potential_ability=max(potential, compute_ability(attrs, position)),
    )


# Salary curve: (ability floor, ability at max salary, exponent). Calibrated so an
# average team's payroll sits near the cap and stars approach the max.
SALARY_FLOOR, SALARY_TOP, SALARY_EXPONENT = 55, 165, 2.0


def market_salary(ability: float, rules: LeagueRules) -> int:
    """Market value per season: steep curve so stars cost far more than role players."""
    max_salary = int(rules.salary_cap * rules.max_salary_cap_fraction)
    x = max(0.0, min(1.0, (ability - SALARY_FLOOR) / (SALARY_TOP - SALARY_FLOOR)))
    salary = rules.min_salary + (max_salary - rules.min_salary) * x ** SALARY_EXPONENT
    return int(round(salary, -4))


def generate_contract(player: Player, league: League, rng: random.Random) -> Contract:
    age = player.age(league.current_date)
    rookie = age <= 22
    years = rng.randint(2, 4) if rookie else rng.randint(1, 5 if age < 30 else 3)
    salary = market_salary(player.current_ability, league.rules)
    salary = int(round(salary * rng.uniform(0.8, 1.2), -4))
    salary = max(league.rules.min_salary,
                 min(salary, int(league.rules.salary_cap * league.rules.max_salary_cap_fraction)))
    option = None
    if years >= 2 and rng.random() < 0.3:
        option = ContractOption.TEAM if rookie else rng.choice(list(ContractOption))
    return Contract(salary, league.season + years - 1, option, rookie_scale=rookie)


# --------------------------------------------------------------------------
# Teams, lineups, schedule
# --------------------------------------------------------------------------
def round_robin(team_ids: list[int]) -> list[list[tuple[int, int]]]:
    """Circle-method rounds; each team plays every other team once."""
    ids = list(team_ids)
    if len(ids) % 2:
        ids.append(None)  # bye
    n = len(ids)
    rounds = []
    for r in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = ids[i], ids[n - 1 - i]
            if a is not None and b is not None:
                pairs.append((a, b) if r % 2 == 0 else (b, a))
        rounds.append(pairs)
        ids = [ids[0], ids[-1], *ids[1:-1]]
    return rounds


def generate_schedule(league: League, rng: random.Random) -> None:
    """Repeat the round robin, flipping home/away each cycle, one round per day."""
    team_ids = sorted(league.teams)
    per_cycle = len(team_ids) - 1
    cycles = max(1, round(league.rules.games_per_season / per_cycle))
    league.rules.games_per_season = cycles * per_cycle
    base = round_robin(team_ids)
    day = 1
    for c in range(cycles):
        rounds = base[:]
        rng.shuffle(rounds)
        for pairs in rounds:
            for home, away in pairs:
                if c % 2:
                    home, away = away, home
                league.add_game(Game(league.next_game_id(), league.season, day, home, away))
            day += 1


def generate_league(*, seed: int = 0, num_teams: int = 12, season: int = 2026,
                    roster_size: int = 13, free_agents: int = 30,
                    scouting_noise: float = 25.0, name: str = "Hoops League") -> League:
    if not 2 <= num_teams <= len(TEAM_NAMES):
        raise ValueError(f"num_teams must be 2-{len(TEAM_NAMES)}")
    rng = random.Random(seed)
    league = League(name=name, season=season, current_date=date(season, 10, 20))  # opening day
    if not league.rules.min_roster <= roster_size <= league.rules.max_roster:
        raise ValueError("roster_size outside league roster limits")

    half = num_teams // 2
    for i, (city, nickname, abbr) in enumerate(rng.sample(TEAM_NAMES, num_teams)):
        conference = "East" if i < half else "West"
        idx = i if i < half else i - half
        size = half if i < half else num_teams - half
        division = f"{conference} {'A' if idx < (size + 1) // 2 else 'B'}"
        league.add_team(Team(
            id=i + 1, city=city, name=nickname, abbreviation=abbr,
            conference=conference, division=division,
            tactics=Tactics(pace=clamp(rng.gauss(10, 3)),
                            three_point_rate=clamp(rng.gauss(10, 3))),
            reputation=int(clamp(rng.gauss(50, 15), 1, 100)),
            budget=int(round(rng.uniform(5, 25) * 1_000_000, -5)),
            arena_capacity=rng.randrange(16000, 21000, 500),
        ))

    # Fill rosters with a snake draft so talent is spread out like a real
    # league, but not evenly: each team's judgement is noisy (``scouting_noise``)
    # and some reach for players. Two of each position plus extras per team.
    position_plan = POSITIONS * 2 + [rng.choice(POSITIONS) for _ in range(roster_size - 10)]
    order = list(league.teams)
    rng.shuffle(order)
    numbers = {tid: rng.sample(range(100), roster_size) for tid in order}
    for rnd, pos in enumerate(position_plan):
        pool = [generate_player(rng, 0, league.current_date, position=pos) for _ in order]
        for tid in (order if rnd % 2 == 0 else order[::-1]):
            pick = max(pool, key=lambda p: p.current_ability + rng.gauss(0, scouting_noise))
            pool.remove(pick)
            pick.id = league.next_player_id()
            pick.team_id = tid
            pick.jersey_number = numbers[tid][rnd]
            pick.contract = generate_contract(pick, league, rng)
            league.add_player(pick)
    for tid in order:
        auto_lineup(league, tid)

    for _ in range(free_agents):
        # Free agents are the leftovers: mostly fringe players, the odd useful vet.
        potential = int(clamp(rng.gauss(75, 15), 40, 125))
        league.add_player(generate_player(rng, league.next_player_id(), league.current_date,
                                          potential=potential))

    # Teenagers in the starting league count as this season's rookies.
    for p in league.players.values():
        if p.age(league.current_date) <= 20:
            p.draft_year = season
    generate_schedule(league, rng)
    return league
