"""Build a hoopsim league file of the real NBA from Basketball-Reference stats.

    python tools/build_nba_league.py [--year 2027] [--out leagues/nba-2026-27.json]

For personal, non-commercial use: the output (``leagues/``) is gitignored and never
part of the game. Pages are fetched politely and cached (``tools/bbref_fetch.py``).

How ratings are made
--------------------
* Stats: this season (early, few games), last season and the one before (at half
  weight), blended by minutes; rate
  stats are shrunk toward an average by sample size (attempts or minutes), so ten
  hot games don't make a shooter.
* Shape: each attribute gets a z-score among NBA players from the stats that show
  it (three-point % and volume for ``three_point``, block % for ``blocking``...),
  then is scaled to the spread of hoopsim's generated leagues. Usage rate is added to
  the scoring ratings (the engines give shots to the best scorers); rebound and assist
  rates count on a log scale, with part of height taken out of rebounding.
* Level: overall ability comes from impact (box plus/minus and minutes, plus salary
  as a prior for players with little court time), mapped onto the generated
  leagues' ability distribution, so the engines stay calibrated: a real league plays
  like a generated one, with real names.
* Scoring ratings bend toward a top (``soft_top``) rather than a hard clamp, so the
  best scorers stay above the next tier.
* Foul drawing (``foul_drawing``): free-throw attempts beyond what his shot mix and
  usage would earn, in the statistical engine's units, shrunk by sample size.
* Potential from age, hidden attributes partly from availability, physique from the
  roster's height and weight. Shot tendencies are what the ratings don't explain:
  set so the statistical engine reproduces each player's real shot mix. Pass-first
  is assists for his share of the ball.

Every rating can be changed in the game's editor afterwards.
"""
from __future__ import annotations

import argparse
import hashlib
import html as htmllib
import json
import math
import os
import random
import re
import statistics
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import bbref_fetch as bf  # noqa: E402
from hoopsim.generate import development_fraction, generate_league  # noqa: E402
from hoopsim.models import (ABILITY_ATTRIBUTES, ATTR_MAX, ATTR_MIN, Attributes,  # noqa: E402
                            Position, compute_ability, derived_drawing_fouls)

# abbreviation on the site -> (shown abbreviation, city, nickname, conference, division)
TEAMS = {
    "BOS": ("BOS", "Boston", "Celtics", "East", "Atlantic"),
    "BRK": ("BKN", "Brooklyn", "Nets", "East", "Atlantic"),
    "NYK": ("NYK", "New York", "Knicks", "East", "Atlantic"),
    "PHI": ("PHI", "Philadelphia", "76ers", "East", "Atlantic"),
    "TOR": ("TOR", "Toronto", "Raptors", "East", "Atlantic"),
    "CHI": ("CHI", "Chicago", "Bulls", "East", "Central"),
    "CLE": ("CLE", "Cleveland", "Cavaliers", "East", "Central"),
    "DET": ("DET", "Detroit", "Pistons", "East", "Central"),
    "IND": ("IND", "Indiana", "Pacers", "East", "Central"),
    "MIL": ("MIL", "Milwaukee", "Bucks", "East", "Central"),
    "ATL": ("ATL", "Atlanta", "Hawks", "East", "Southeast"),
    "CHO": ("CHA", "Charlotte", "Hornets", "East", "Southeast"),
    "MIA": ("MIA", "Miami", "Heat", "East", "Southeast"),
    "ORL": ("ORL", "Orlando", "Magic", "East", "Southeast"),
    "WAS": ("WAS", "Washington", "Wizards", "East", "Southeast"),
    "DEN": ("DEN", "Denver", "Nuggets", "West", "Northwest"),
    "MIN": ("MIN", "Minnesota", "Timberwolves", "West", "Northwest"),
    "OKC": ("OKC", "Oklahoma City", "Thunder", "West", "Northwest"),
    "POR": ("POR", "Portland", "Trail Blazers", "West", "Northwest"),
    "UTA": ("UTA", "Utah", "Jazz", "West", "Northwest"),
    "GSW": ("GSW", "Golden State", "Warriors", "West", "Pacific"),
    "LAC": ("LAC", "Los Angeles", "Clippers", "West", "Pacific"),
    "LAL": ("LAL", "Los Angeles", "Lakers", "West", "Pacific"),
    "PHO": ("PHX", "Phoenix", "Suns", "West", "Pacific"),
    "SAC": ("SAC", "Sacramento", "Kings", "West", "Pacific"),
    "DAL": ("DAL", "Dallas", "Mavericks", "West", "Southwest"),
    "HOU": ("HOU", "Houston", "Rockets", "West", "Southwest"),
    "MEM": ("MEM", "Memphis", "Grizzlies", "West", "Southwest"),
    "NOP": ("NOP", "New Orleans", "Pelicans", "West", "Southwest"),
    "SAS": ("SAS", "San Antonio", "Spurs", "West", "Southwest"),
}
BIG_MARKETS = {"NYK": 12, "LAL": 12, "GSW": 8, "BOS": 8, "CHI": 6, "LAC": 4, "MIA": 4,
               "BRK": 4, "PHI": 3, "DAL": 3, "HOU": 3, "TOR": 3}
COUNTRIES = {"us": "USA", "ca": "Canada", "fr": "France", "rs": "Serbia", "au": "Australia",
             "de": "Germany", "es": "Spain", "gr": "Greece", "si": "Slovenia", "ng": "Nigeria",
             "cm": "Cameroon", "lt": "Lithuania", "hr": "Croatia", "tr": "Turkey", "il": "Israel",
             "jp": "Japan", "gb": "Great Britain", "it": "Italy", "br": "Brazil", "ar": "Argentina",
             "do": "Dominican Republic", "bs": "Bahamas", "lv": "Latvia", "fi": "Finland",
             "pt": "Portugal", "ss": "South Sudan", "cd": "DR Congo", "ba": "Bosnia",
             "me": "Montenegro", "ua": "Ukraine", "nz": "New Zealand", "ch": "Switzerland",
             "se": "Sweden", "cz": "Czechia", "pl": "Poland", "jm": "Jamaica", "sn": "Senegal",
             "mx": "Mexico", "ge": "Georgia", "at": "Austria", "be": "Belgium", "nl": "Netherlands",
             "cn": "China", "ml": "Mali", "gn": "Guinea", "ph": "Philippines", "lc": "Saint Lucia"}

# Approximate NBA 2026-27 salary rules (the editor and league settings can change them).
OLDER_WEIGHT = 0.5
# Shot load: the engines hand shots to the best scorers, so a player's scoring ratings
# also set how many shots he takes. Usage (z-score of log usage rate) is added to them, so
# a 30%-usage star out-shoots a 15%-usage spot-up shooter with the same percentages.
# Threes get half: a three rating also sets three-point volume and accuracy.
LOAD = {"finishing": 1.0, "close_range": 1.0, "mid_range": 1.0, "three_point": 0.5}
THREE_VOLUME = 0.25          # three_point: 75% accuracy, 25% volume (volume is a tendency)
# The engines already favour taller rebounders; take part of the height out of the
# rebound ratings so bigs' boards aren't counted twice.
REBOUND_HEIGHT_CM = 32       # cm of height per unit of log rebound rate
# Scoring ratings (the LOAD ones) bend toward a top z-score instead of a hard clamp, so
# the league's best scorers stay a notch above the next 30 (with a hard 2.8 clamp,
# about 60 players shared the top scoring ratings).
SCORING_Z_KNEE, SCORING_Z_TOP = 2.0, 3.6
# Foul drawing: free-throw rates are shrunk toward the league by this many attempts, and
# players with few shots lean on their ratings (``foul_drawing``).
FT_PRIOR_FTA = 40
FT_PRIOR_FGA = 150
WITH_HISTORY = True
RULES = {"salary_cap": 165_000_000, "luxury_tax_line": 201_000_000, "min_salary": 1_270_000,
         "mid_level_exception": 14_100_000, "max_roster": 15, "min_roster": 13}


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
_CELL = re.compile(r'<(t[dh])([^>]*)data-stat="([^"]+)"([^>]*)>(.*?)</\1>', re.S)
_ID = re.compile(r"/players/\w/([\w.-]+)\.html")


def _text(cell: str) -> str:
    return htmllib.unescape(re.sub(r"<[^>]+>", "", cell)).replace("\xa0", " ").strip()


def table(page: str, table_id: str) -> list[dict]:
    """Rows of one table as {data-stat: text}; '_id' is the player's site id,
    '_class:<stat>' a cell's class (option markers on contracts)."""
    page = page.replace("<!--", "").replace("-->", "")
    m = re.search(r'<table[^>]*id="%s".*?</table>' % re.escape(table_id), page, re.S)
    if not m:
        return []
    body = m.group(0).split("<tbody", 1)[-1]
    out = []
    for row in re.findall(r"<tr([^>]*)>(.*?)</tr>", body, re.S):
        attrs, inner = row
        if "thead" in attrs or "<td" not in inner:
            continue
        r: dict = {}
        for _tag, pre, stat, post, content in _CELL.findall(inner):
            r[stat] = _text(content)
            cls = re.search(r'class="([^"]*)"', pre + post)
            if cls:
                r["_class:" + stat] = cls.group(1)
            pid = _ID.search(content) or re.search(r'data-append-csv="([^"]+)"', pre + post)
            if pid and "_id" not in r:
                r["_id"] = pid.group(1)
            if stat in ("player", "name_display") and "(TW)" in inner:
                r["_two_way"] = True
        if r:
            out.append(r)
    return out


def num(x, default=0.0) -> float:
    try:
        return float(str(x).replace(",", "").replace("$", "").replace("%", ""))
    except ValueError:
        return default


def by_player(rows: list[dict]) -> dict[str, dict]:
    """One row per player: the combined line of a traded player (2TM/3TM), else his only row."""
    out: dict[str, dict] = {}
    for r in rows:
        pid = r.get("_id")
        if not pid:
            continue
        team = r.get("team_name_abbr", "")
        if pid not in out or re.fullmatch(r"\dTM|TOT", team):
            out[pid] = r
    return out


# --------------------------------------------------------------------------
# Stats: blend two seasons
# --------------------------------------------------------------------------
def season_stats(year: int) -> dict[str, dict]:
    pg = by_player(table(bf.get(f"NBA_{year}_per_game.html", ""), "per_game_stats"))
    adv = by_player(table(bf.get(f"NBA_{year}_advanced.html", ""), "advanced"))
    sh = by_player(table(bf.get(f"NBA_{year}_shooting.html", ""), "shooting"))
    out = {}
    for pid, p in pg.items():
        g = num(p.get("games"))
        mp = num(p.get("mp_per_g")) * g
        if mp <= 0:
            continue
        a, s = adv.get(pid, {}), sh.get(pid, {})
        tot = {k: num(p.get(k + "_per_g")) * g for k in
               ("fg", "fga", "fg3", "fg3a", "ft", "fta", "orb", "drb", "ast", "stl", "blk", "tov", "pf", "pts")}
        fga = tot["fga"]
        zones = {}
        for z in ("00_03", "03_10", "10_16", "16_xx"):
            share = num(s.get("pct_fga_" + z))
            zones[z] = (share * fga, num(s.get("fg_pct_" + z)) * share * fga)   # attempts, makes
        out[pid] = {
            "name": p.get("name_display", ""), "g": g, "gs": num(p.get("games_started")), "mp": mp,
            "tot": tot, "zones": zones, "dunk_share": num(s.get("pct_fga_dunk")),
            "ast_fg2": num(s.get("pct_ast_fg2")),
            **{k: num(a.get(k)) for k in ("per", "ts_pct", "orb_pct", "drb_pct", "ast_pct", "stl_pct",
                                          "blk_pct", "tov_pct", "usg_pct", "ws_per_48", "obpm",
                                          "dbpm", "bpm")},
        }
    return out


def scaled(st: dict, w: float) -> dict:
    """A season counted at weight ``w`` (an older season counts for less)."""
    return {**st, "g": st["g"] * w, "gs": st["gs"] * w, "mp": st["mp"] * w,
            "tot": {k: v * w for k, v in st["tot"].items()},
            "zones": {z: (a * w, m * w) for z, (a, m) in st["zones"].items()}}


def blend(cur: dict, prev: dict) -> dict:
    """Minutes-weighted mix of two seasons: counting stats add up, rates are averaged by minutes."""
    if not prev:
        return cur
    if not cur:
        return prev
    w1, w0 = cur["mp"], prev["mp"]
    W = w1 + w0
    out = {"name": cur["name"], "g": cur["g"] + prev["g"], "gs": cur["gs"] + prev["gs"], "mp": W,
           "tot": {k: cur["tot"][k] + prev["tot"][k] for k in cur["tot"]},
           "zones": {z: (cur["zones"][z][0] + prev["zones"][z][0], cur["zones"][z][1] + prev["zones"][z][1])
                     for z in cur["zones"]}}
    for k in ("dunk_share", "ast_fg2", "per", "ts_pct", "orb_pct", "drb_pct", "ast_pct", "stl_pct",
              "blk_pct", "tov_pct", "usg_pct", "ws_per_48", "obpm", "dbpm", "bpm"):
        out[k] = (cur[k] * w1 + prev[k] * w0) / W
    return out


def shrink(value: float, n: float, prior: float, k: float) -> float:
    """``value`` measured on ``n`` trials, pulled toward ``prior`` as if ``k`` more trials were average."""
    return (value * n + prior * k) / (n + k)


# --------------------------------------------------------------------------
# Ratings
# --------------------------------------------------------------------------
def zscores(values: dict[str, float]) -> dict[str, float]:
    xs = list(values.values())
    mu, sd = statistics.fmean(xs), statistics.pstdev(xs) or 1.0
    return {k: (v - mu) / sd for k, v in values.items()}


def raw_features(p: dict) -> dict[str, float]:
    """One number per attribute (higher = better), before standardising."""
    st = p["stats"]
    t, mp = st["tot"], max(st["mp"], 1.0)
    per36 = lambda x: 36 * x / mp  # noqa: E731
    h, w = p["height_cm"], p["weight_kg"]
    def zone(z, prior, k=40):
        a, m = st["zones"][z]
        return shrink(m / a if a else prior, a, prior, k)
    three = shrink(t["fg3"] / t["fg3a"] if t["fg3a"] else 0.30, t["fg3a"], 0.33, 150)
    ft = shrink(t["ft"] / t["fta"] if t["fta"] else 0.72, t["fta"], 0.75, 80)
    rim, short = zone("00_03", 0.66), zone("03_10", 0.42)
    mid = (zone("10_16", 0.42) * 0.5 + zone("16_xx", 0.40) * 0.5)
    mins = lambda x, prior, k=600: shrink(x, mp, prior, k)  # noqa: E731
    ast, tov, usg = mins(st["ast_pct"], 12), mins(st["tov_pct"], 13), mins(st["usg_pct"], 18)
    stl, blk = mins(st["stl_pct"], 1.4), mins(st["blk_pct"], 1.6)
    orb, drb = mins(st["orb_pct"], 6), mins(st["drb_pct"], 15)
    obpm, dbpm, bpm = mins(st["obpm"], -1.5, 900), mins(st["dbpm"], -0.5, 900), mins(st["bpm"], -2, 900)
    ws48 = mins(st["ws_per_48"], 0.07, 900)
    pf36 = shrink(per36(t["pf"]), mp, 3.2, 600)
    fta36, fg3a36 = per36(t["fta"]), per36(t["fg3a"])
    mpg = st["mp"] / max(st["g"], 1)
    age = p["age"]
    youth = -max(0, age - 28)                         # athleticism fades in the thirties
    return {
        "three_point": (1 - THREE_VOLUME) * three / 0.035 + THREE_VOLUME * math.log1p(fg3a36) / 0.5,
        "free_throw": ft,
        "mid_range": mid / 0.05 + 0.6 * ft / 0.08,
        "close_range": short / 0.06 + 0.3 * math.log1p(per36(st["zones"]["03_10"][0])),
        "finishing": rim / 0.06 + 0.5 * math.log1p(fta36) + 2.0 * st["dunk_share"],
        "passing": math.log(max(ast, 3)),
        "vision": math.log(max(ast, 3)) / 0.5 - 0.3 * tov / 4 + 0.2 * obpm / 2,
        "ball_handling": ast / 8 + usg / 8 - tov / 5 - (h - 200) / 12,
        "perimeter_defense": dbpm / 1.5 + stl / 0.6 - (h - 203) / 25 - max(0, h - 211) / 6,
        "interior_defense": dbpm / 1.5 + blk / 1.5 + drb / 8 + (h - 203) / 10,
        "stealing": stl,
        "blocking": blk,
        "offensive_rebounding": math.log(max(orb, 0.5)) - (h - 203) / REBOUND_HEIGHT_CM,
        "defensive_rebounding": math.log(max(drb, 3)) - (h - 203) / REBOUND_HEIGHT_CM,
        "speed": -(h - 200) / 8 - (w - 100) / 15 + stl / 1.5 + youth / 3,
        "strength": (w - 100) / 10 + (w / (h / 100) ** 2 - 25) / 2,
        "vertical": blk / 2 + 3 * st["dunk_share"] + youth / 3 - (w - 105) / 25,
        "stamina": mpg / 6 + youth / 4,
        "basketball_iq": bpm / 2 + ws48 / 0.05 + (ast / max(tov, 4)) / 2 - pf36 / 2,
        "composure": ft / 0.08 - tov / 5 + min(p["experience"], 12) / 6,
        "_load": math.log(max(usg, 8)),
    }


def impact(p: dict) -> float:
    """Who's good: shrunk box plus/minus and playing time, with salary as the prior for
    players we haven't seen much of (rookies, players back from injury)."""
    st = p["stats"]
    mp = st["mp"] if st else 0.0
    mpg = st["mp"] / max(st["g"], 1) if st else 0.0
    bpm = shrink(st["bpm"], mp, -2.5, 1200) if st else -2.5
    sal = math.log(max(p["salary"], RULES["min_salary"]) / RULES["min_salary"])   # 0..~3.9
    seen = mp / (mp + 1500)
    return seen * (bpm + 0.10 * mpg) + (1 - seen) * (-2.5 + 1.2 * sal) + 0.35 * sal


def generated_targets(free_agents: int, seed: int = 7) -> tuple[list[int], dict[str, tuple[float, float]]]:
    """The ability distribution and per-attribute mean/sd of a generated 30-team league."""
    lg = generate_league(seed=seed, num_teams=30, roster_size=15, free_agents=free_agents)
    ps = [p for p in lg.players.values() if p.team_id is not None]
    abil = sorted(p.current_ability for p in lg.players.values())   # free agents too
    spread = {}
    for n in Attributes.names():
        xs = [getattr(p.attributes, n) for p in ps]
        spread[n] = (statistics.fmean(xs), statistics.pstdev(xs))
    return abil, spread


def make_attributes(z: dict[str, float], spread, position: Position, target: int) -> dict[str, int]:
    """The ratings that make up ability (drawing_fouls is rated on its own: foul_drawing)."""
    def build(shift: float) -> dict[str, int]:
        return {n: int(max(ATTR_MIN, min(ATTR_MAX, round(spread[n][0] + spread[n][1] * z[n] + shift))))
                for n in ABILITY_ATTRIBUTES}
    lo, hi = -15.0, 15.0
    for _ in range(40):                 # find the shift that lands on the target ability
        mid = (lo + hi) / 2
        if compute_ability(Attributes(**build(mid)), position) < target:
            lo = mid
        else:
            hi = mid
    return build(hi)


def soft_top(z: float) -> float:
    """A scoring z-score bent toward ``SCORING_Z_TOP`` past ``SCORING_Z_KNEE`` (slope 1 at the
    knee, as the engine's usage ceiling): the best scorers stay above the next tier
    instead of all sitting at one clamp."""
    if z <= SCORING_Z_KNEE:
        return z
    room = SCORING_Z_TOP - SCORING_Z_KNEE
    return SCORING_Z_KNEE + room * (1 - math.exp(-(z - SCORING_Z_KNEE) / room))


def _foul_shots(st: dict) -> float:
    """Free throws a player's shot mix earns at the statistical engine's base rates:
    shooting-foul odds by zone times the free throws a foul gives (2, 3 from deep)."""
    from hoopsim.engine import CLOSE, DEFAULT_TUNING, MID, RIM, THREE
    base = DEFAULT_TUNING.shooting_foul
    z = st["zones"]
    return (2 * (base[RIM] * z["00_03"][0] + base[CLOSE] * z["03_10"][0]
                 + base[MID] * (z["10_16"][0] + z["16_xx"][0])) + 3 * base[THREE] * st["tot"]["fg3a"])


def _engine_share(usage: float) -> float:
    """About the share of the shots the statistical engine gives a player with this real
    usage rate: past the knee his share levels off (``engine.usage_ceiling``)."""
    from hoopsim.engine import DEFAULT_TUNING as T
    knee, room = T.usage_knee, T.usage_ceiling - T.usage_knee
    return usage if usage <= knee else knee + room * (1 - math.exp(-(usage - knee) / room))


def foul_drawing(players: list[dict]) -> list[int]:
    """drawing_fouls for each player: what his free-throw rate says beyond his shot mix
    and his share of the shots, in the statistical engine's units.

    The rate is free-throw attempts against the attempts his shots would earn at the
    league's rate for their mix (``_foul_shots``: a rim attacker is expected to get to the
    line more than a spot-up shooter), shrunk toward the league by ``FT_PRIOR_FTA``
    attempts. The engine already gives a high-usage scorer more whistles
    (``Tuning.foul_draw``, on the share the engine will give him), so that part comes
    out too. What is left is the rating's
    log effect (``Tuning.foul_drawing`` a point), centred so that the shot-weighted
    rating is the engine's ``foul_drawing_centre``: team free throws stay where a
    generated league has them. Without a sample the rating follows his other ratings,
    as for old saves (``derived_drawing_fouls``)."""
    from hoopsim.engine import DEFAULT_TUNING as T
    stats = [p["stats"] for p in players]
    league_fta = sum(st["tot"]["fta"] for st in stats)
    league_expected = sum(_foul_shots(st) for st in stats)
    fga_all = sum(st["tot"]["fga"] for st in stats)
    resid, weight = [], []
    for st in stats:
        t = st["tot"]
        expected = _foul_shots(st) * league_fta / league_expected
        ratio = (t["fta"] + FT_PRIOR_FTA) / (expected + FT_PRIOR_FTA)
        share = _engine_share(shrink(st["usg_pct"], st["mp"], 18, 600) / 100)
        resid.append(math.log(ratio) - T.foul_draw * share)
        weight.append(t["fga"])
    offset = T.foul_drawing_centre - sum(r * w for r, w in zip(resid, weight)) / T.foul_drawing / fga_all
    out = []
    for p, r in zip(players, resid):
        from_stats = offset + r / T.foul_drawing
        seen = p["stats"]["tot"]["fga"] / (p["stats"]["tot"]["fga"] + FT_PRIOR_FGA)
        prior = derived_drawing_fouls(Attributes(**p["attributes"]))
        out.append(int(max(ATTR_MIN, min(ATTR_MAX, round(seen * from_stats + (1 - seen) * prior)))))
    return out


# --------------------------------------------------------------------------
# The league
# --------------------------------------------------------------------------
def _rng(pid: str) -> random.Random:
    return random.Random(int(hashlib.sha1(pid.encode()).hexdigest()[:12], 16))


def _height_cm(s: str) -> int:
    m = re.match(r"(\d+)-(\d+)", s or "")
    return round((int(m.group(1)) * 12 + int(m.group(2))) * 2.54) if m else 200


def _contracts(season: int) -> dict[str, dict]:
    out = {}
    for r in table(bf.get("contracts_players.html", ""), "player-contracts"):
        pid = r.get("_id")
        if not pid or pid in out:
            continue
        years = [(i, num(r.get(f"y{i}"))) for i in range(1, 7) if num(r.get(f"y{i}")) > 0]
        if not years or years[0][0] != 1:
            continue
        last_i = years[-1][0]
        cls = r.get(f"_class:y{last_i}", "")
        option = "player" if "salary-pl" in cls else "team" if "salary-tm" in cls else None
        out[pid] = {"salary": int(years[0][1]), "expires_season": season + last_i - 1, "option": option}
    return out


# --------------------------------------------------------------------------
# Career history: every past season, regular season and playoffs, with honours
# --------------------------------------------------------------------------
# Old team codes -> today's franchise (for the team link); the old code is still shown.
FRANCHISE = {"NJN": "BRK", "SEA": "OKC", "NOH": "NOP", "NOK": "NOP", "CHA": "CHO", "CHH": "CHO", "VAN": "MEM"}
SHOWN = {"BRK": "BKN", "CHO": "CHA", "PHO": "PHX"}
AWARD_WINS = {"MVP": "mvp", "DPOY": "dpoy", "ROY": "roy", "6MOY": "sixth_man", "SMOY": "sixth_man",
              "MIP": "mip", "CPOY": "clutch_poy"}


def _honours(text: str) -> dict:
    out = {}
    for tok in (t.strip() for t in text.split(",") if t.strip()):
        if tok == "AS":
            out["all_star"] = True
        elif tok[:3] == "NBA" and tok[3:].isdigit():
            out["all_nba"] = int(tok[3:])
        elif tok[:3] == "DEF" and tok[3:].isdigit():
            out["all_defense"] = int(tok[3:])
        elif "-" in tok:
            name, place = tok.rsplit("-", 1)
            if place == "1" and name in AWARD_WINS:
                out[AWARD_WINS[name]] = True
    return out


def _career_lines(rows: list[dict], season: int, playoffs: bool) -> dict[str, dict]:
    """One line per player for a season: a traded player's combined line, under his last team."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("_id"):
            by.setdefault(r["_id"], []).append(r)
    out = {}
    for pid, rs in by.items():
        total = next((r for r in rs if re.fullmatch(r"\dTM|TOT", r.get("team_name_abbr", ""))), rs[0])
        teams = [r.get("team_name_abbr", "") for r in rs if not re.fullmatch(r"\dTM|TOT", r.get("team_name_abbr", ""))]
        site = teams[-1] if teams else total.get("team_name_abbr", "")
        n = lambda k: int(num(total.get(k)))  # noqa: E731
        line = {"season": season, "playoffs": playoffs, "team": SHOWN.get(site, site),
                "franchise": SHOWN.get(FRANCHISE.get(site, site), FRANCHISE.get(site, site)),
                "age": n("age"), "gp": n("games"), "gs": n("games_started"), "min": n("mp"), "pts": n("pts"),
                "fgm": n("fg"), "fga": n("fga"), "tpm": n("fg3"), "tpa": n("fg3a"), "ftm": n("ft"), "fta": n("fta"),
                "orb": n("orb"), "drb": n("drb"), "ast": n("ast"), "stl": n("stl"), "blk": n("blk"),
                "tov": n("tov"), "pf": n("pf")}
        if not playoffs:
            line.update(_honours(" ".join(r.get("awards", "") for r in rs)))
        out[pid] = line
    return out


def careers(year: int) -> dict[str, list[dict]]:
    """{site id: [career lines]} from the cached season pages before ``year``."""
    out: dict[str, list[dict]] = {}
    for y in range(bf.FIRST_HISTORY, year):
        f = bf.CACHE / f"NBA_{y}_totals.html"
        if not f.exists():
            continue
        page = f.read_text(encoding="utf-8")
        for table_id, playoffs in (("totals_stats", False), ("totals_stats_post", True)):
            for pid, line in _career_lines(table(page, table_id), y - 1, playoffs).items():
                if line["gp"] > 0:
                    out.setdefault(pid, []).append(line)
    for lines in out.values():
        lines.sort(key=lambda c: (c["season"], c["playoffs"]))
    return out


def drafts(year: int) -> dict[str, tuple[int, int]]:
    """{site id: (draft year, overall pick)}."""
    out = {}
    for y in range(bf.FIRST_HISTORY - 1, year):
        f = bf.CACHE / f"draft_NBA_{y}.html"
        if not f.exists():
            continue
        for r in table(f.read_text(encoding="utf-8"), "stats"):
            if r.get("_id") and r.get("pick_overall", "").isdigit():
                out.setdefault(r["_id"], (y, int(r["pick_overall"])))
    return out


def build(year: int) -> dict:
    season = year - 1                               # hoopsim names a season by its first year
    today = date(season, 10, 20)
    cur, prev = season_stats(year), season_stats(year - 1)
    # Two seasons ago, at half weight: players who missed last season hurt aren't judged on a few games.
    older = {pid: scaled(st, OLDER_WEIGHT) for pid, st in season_stats(year - 2).items()}
    contracts = _contracts(season)

    players, teams = [], []
    on_roster = set()
    for site, (abbr, city, nick, conf, div) in TEAMS.items():
        page = bf.get(f"team_{site}_{year}.html", "")
        misc = table(page, "team_misc")
        roster = table(page, "roster")
        if not roster:
            raise SystemExit(f"no roster for {site}: run tools/bbref_fetch.py first")
        teams.append({"site": site, "abbreviation": abbr, "city": city, "name": nick,
                      "conference": conf, "division": div,
                      "pace": num(misc[0].get("pace")) if misc else 99.0,
                      "fg3a_rate": num(misc[0].get("fg3a_per_fga_pct")) if misc else 0.40})
        for r in roster:
            pid = r.get("_id")
            if not pid or pid in on_roster:
                continue
            on_roster.add(pid)
            players.append(_player(pid, r, site, cur, prev, older, contracts, season, today))

    # Unsigned veterans who played last season: the free-agent pool.
    for pid, st in sorted(prev.items(), key=lambda kv: -kv[1]["mp"]):
        if pid in on_roster or len([p for p in players if p["_site"] is None]) >= 40:
            continue
        if st["mp"] < 300:
            break
        players.append(_player(pid, {"player": st["name"], "pos": ""}, None, cur, prev, older, {}, season, today))

    # Standard roster spots: 15 per team (two-way and the lowest-paid extras become free agents).
    for t in teams:
        mine = [p for p in players if p["_site"] == t["site"]]
        mine.sort(key=lambda p: (p["_two_way"], -p["salary"], -p["_impact"]))
        for p in mine[RULES["max_roster"]:]:
            p["_site"] = None

    abil_dist, spread = generated_targets(sum(1 for p in players if p["_site"] is None))
    _rate(players, abil_dist, spread, today)
    by_site = {}
    for t in teams:
        mine = [p for p in players if p["_site"] == t["site"]]
        by_site[t["site"]] = mine
    return _league_file(year, season, today, teams, players, by_site)


def _player(pid, r, site, cur, prev, older, contracts, season, today) -> dict:
    name = r.get("player") or r.get("name_display") or ""
    name = re.sub(r"\s*\(TW\)\s*", "", name).strip()
    first, _, last = name.partition(" ")
    st = None
    for season_line in (cur.get(pid), prev.get(pid), older.get(pid)):
        if season_line:
            st = blend(st, season_line) if st else season_line
    born = None
    try:
        born = datetime.strptime(r.get("birth_date", ""), "%B %d, %Y").date()
    except ValueError:
        pass
    rng = _rng(pid)
    if born is None:
        born = date(today.year - 27, 1 + rng.randrange(12), 1 + rng.randrange(28))
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    exp = r.get("years_experience", "")
    experience = 0 if exp in ("R", "") else int(num(exp))
    pos = (r.get("pos") or "").split("-")[0] or "SF"
    flag = r.get("flag", "").strip().split(" ")[0].lower() if r.get("flag") else ""
    c = contracts.get(pid)
    two_way = bool(r.get("_two_way"))
    salary = c["salary"] if c else RULES["min_salary"]
    p = {
        "_id": pid, "_site": site, "_two_way": two_way, "stats": st, "age": age, "experience": experience,
        "first_name": first, "last_name": last or first,
        "birth_date": born.isoformat(), "nationality": COUNTRIES.get(flag, flag.upper() or "USA"),
        "position": pos if pos in {"PG", "SG", "SF", "PF", "C"} else "SF",
        "height_cm": _height_cm(r.get("height", "")) if r.get("height") else {"PG": 191, "SG": 196, "SF": 201, "PF": 206, "C": 211}.get(pos, 201),
        "weight_kg": round(num(r.get("weight"), 215) * 0.4536),
        "jersey_number": int(r["number"]) if r.get("number", "").isdigit() else None,
        "salary": salary,
        "contract": {"salary": salary, "expires_season": c["expires_season"] if c else season,
                     "option": c["option"] if c else None,
                     "rookie_scale": experience <= 3 and bool(c) and salary > 2 * RULES["min_salary"]},
    }
    p["_impact"] = impact(p)
    return p


def _rate(players: list[dict], abil_dist: list[int], spread, today: date) -> None:
    feats = {}
    for p in players:
        if p["stats"] is None:          # no NBA minutes yet: an average body at his size
            p["stats"] = {"name": "", "g": 0, "gs": 0, "mp": 0.0,
                          "tot": {k: 0.0 for k in ("fg", "fga", "fg3", "fg3a", "ft", "fta", "orb", "drb",
                                                   "ast", "stl", "blk", "tov", "pf", "pts")},
                          "zones": {z: (0.0, 0.0) for z in ("00_03", "03_10", "10_16", "16_xx")},
                          "dunk_share": 0.0, "ast_fg2": 0.5,
                          **{k: 0.0 for k in ("per", "ts_pct", "orb_pct", "drb_pct", "ast_pct", "stl_pct",
                                              "blk_pct", "tov_pct", "usg_pct", "ws_per_48", "obpm",
                                              "dbpm", "bpm")}}
            _position_prior(p)
        feats[p["_id"]] = raw_features(p)
    zs = {n: zscores({pid: f[n] for pid, f in feats.items()}) for n in (*ABILITY_ATTRIBUTES, "_load")}
    # Ability: impact rank onto the generated distribution (rostered players), free agents below.
    ranked = sorted(players, key=lambda p: p["_impact"])
    n = len(ranked)
    for i, p in enumerate(ranked):
        q = (i + 0.5) / n
        target = abil_dist[min(len(abil_dist) - 1, int(q * len(abil_dist)))]
        pos = Position(p["position"])
        load = zs["_load"][p["_id"]]
        z = {k: max(-2.8, min(2.8, zs[k][p["_id"]] + LOAD.get(k, 0.0) * load))
             for k in ABILITY_ATTRIBUTES}
        for k in LOAD:                   # the scoring ratings bend toward their top instead
            z[k] = max(-2.8, soft_top(zs[k][p["_id"]] + LOAD[k] * load))
        attrs = make_attributes(z, spread, pos, target)
        p["attributes"] = attrs
        ca = compute_ability(Attributes(**attrs), pos)
        rng = _rng(p["_id"])
        if p["age"] < 27:                # still growing: the development curve says how much
            pa = ca / min(1.0, development_fraction(p["age"])) * rng.uniform(0.9, 1.12)
        else:                            # peaked: a little headroom at most
            pa = ca + rng.uniform(0, 4 if p["age"] < 31 else 1)
        p["potential_ability"] = int(max(ca, min(200, round(pa))))
        p["hidden"] = _hidden(p, rng)
        p["wingspan_cm"] = int(round(p["height_cm"] + 4 + rng.gauss(4, 3)))
        p["secondary_positions"] = _secondary(p["position"], p["height_cm"])
    league_shots = _league_shots(players)
    for p in players:
        p["tendencies"] = _tendencies(p, p["attributes"], league_shots)
    for p, df in zip(players, foul_drawing(players)):
        p["attributes"]["drawing_fouls"] = df


def _league_shots(players: list[dict]) -> dict:
    """The league's shot mix (share of attempts by zone) and its shooters' average zone
    ratings, weighted by attempts."""
    tot = {"rim": 0.0, "close": 0.0, "mid": 0.0, "three": 0.0}
    rating = dict.fromkeys(tot, 0.0)
    for p in players:
        prof, fga = shot_profile(p["stats"]), p["stats"]["tot"]["fga"]
        for z in tot:
            tot[z] += prof[z] * fga if prof else 0.0
            rating[z] += _zone_rating(p["attributes"], z) * fga if prof else 0.0
    n = sum(tot.values())
    return {"share": {z: v / n for z, v in tot.items()}, "rating": {z: v / n for z, v in rating.items()}}


def _position_prior(p) -> None:
    """Typical rates for a player's position, so rookies without minutes aren't all alike."""
    st, pos = p["stats"], p["position"]
    table_ = {"PG": (28, 1.8, 0.6, 2.0, 11), "SG": (14, 1.5, 0.8, 2.0, 11), "SF": (11, 1.4, 1.2, 4.0, 14),
              "PF": (9, 1.2, 2.2, 6.5, 18), "C": (8, 1.1, 3.5, 9.5, 22)}
    st["ast_pct"], st["stl_pct"], st["blk_pct"], st["orb_pct"], st["drb_pct"] = table_[pos]


# How the statistical engine turns ratings into a shot mix (engine._GameSim._zone_weights):
# zone weight = base mix * exp(rating slope * skill impact * (rating - 10))
#                        * exp(tendency slope * (tendency - 10)).
ZONE_RATING_SLOPE = {"rim": 0.25, "close": 0.25, "mid": 0.25, "three": 0.35}
ZONE_TENDENCY = {"rim": ("drive", 0.05), "close": ("post_up", 0.04), "mid": ("mid_range", 0.07),
                 "three": ("three_point", 0.06)}
SHOT_PRIOR_FGA = 40           # shot profiles are shrunk toward the league's by this many attempts


def _zone_rating(a: dict, zone: str) -> float:
    if zone == "rim":
        return 0.7 * a["finishing"] + 0.15 * a["speed"] + 0.15 * a["ball_handling"]
    return a[{"close": "close_range", "mid": "mid_range", "three": "three_point"}[zone]]


def shot_profile(st: dict) -> dict[str, float] | None:
    """Share of field-goal attempts by zone (rim, close, mid, three), or None without a sample."""
    fga = st["tot"]["fga"]
    if fga < 40:
        return None
    z = {k: st["zones"][k][0] / fga for k in st["zones"]}
    return {"rim": z["00_03"], "close": z["03_10"], "mid": z["10_16"] + z["16_xx"],
            "three": st["tot"]["fg3a"] / fga}


def shot_tendencies(real: dict[str, float], fga: float, league: dict, a: dict) -> dict[str, int]:
    """Drive, post-up, mid-range and three tendencies that reproduce a player's real shot mix.

    Ratings already tilt a player's shots (a great shooter takes more threes), so the
    tendency is the preference left over: how his shot mix differs from the league's
    beyond what his ratings, against the league's average ratings, explain.
    ``league`` is {"share": zone -> share of attempts, "rating": zone -> mean rating}.
    """
    from hoopsim.engine import DEFAULT_TUNING
    impact = DEFAULT_TUNING.skill_impact
    lshare, lrating = league["share"], league["rating"]
    share = {z: (real[z] * fga + lshare[z] * SHOT_PRIOR_FGA) / (fga + SHOT_PRIOR_FGA) for z in real}
    need = {z: math.log(max(share[z], 0.005) / lshare[z])
               - ZONE_RATING_SLOPE[z] * impact * (_zone_rating(a, z) - lrating[z]) for z in ZONE_TENDENCY}
    # Tendency t_z must satisfy slope_z * (t_z - 10) = need_z + k for one shared k (only
    # relative weights matter); pick k to keep tendencies as close to 10 as possible.
    k = (-sum(need[z] / slope ** 2 for z, (_, slope) in ZONE_TENDENCY.items())
         / sum(1 / slope ** 2 for _, slope in ZONE_TENDENCY.values()))
    return {name: int(max(ATTR_MIN, min(ATTR_MAX, round(10 + (need[z] + k) / slope))))
            for z, (name, slope) in ZONE_TENDENCY.items()}


def _tendencies(p, a, league_shots=None) -> dict[str, int]:
    st = p["stats"]
    fga = st["tot"]["fga"]
    def share(z):
        return st["zones"][z][0] / fga if fga else None
    def scale(x, mid, sd, fallback):
        return int(max(ATTR_MIN, min(ATTR_MAX, round(10 + 3 * (x - mid) / sd)))) if x is not None else fallback
    from hoopsim.models import Attributes as A, Tendencies
    base = Tendencies.from_attributes(A(**a)).to_dict()
    three = st["tot"]["fg3a"] / fga if fga >= 40 else None
    mid = ((share("10_16") or 0) + (share("16_xx") or 0)) if fga >= 40 else None
    rim = share("00_03") if fga >= 40 else None
    post = share("03_10") if fga >= 40 else None
    out = {
        "three_point": scale(three, 0.40, 0.11, base["three_point"]),
        "mid_range": scale(mid, 0.14, 0.08, base["mid_range"]),
        "drive": scale(None if rim is None else rim * (1 - st["ast_fg2"]) + st["usg_pct"] / 100, 0.28, 0.10, base["drive"]),
        "post_up": scale(post if p["position"] in ("PF", "C") else (post or 0) * 0.6 if post is not None else None,
                         0.15, 0.07, base["post_up"]),
        # Pass-first means assists for his share of the ball: a creator who scores and
        # passes (high assist and usage rates) isn't pass-first, and the statistical
        # engine would otherwise take shots away from him.
        "passing": scale(math.log(max(st["ast_pct"], 3) / max(st["usg_pct"], 8)) if fga >= 40 else None,
                         -0.30, 0.43, base["passing"]),
        "cutting": scale(None if rim is None else rim * st["ast_fg2"], 0.18, 0.09, base["cutting"]),
    }
    profile = shot_profile(st)
    if profile and league_shots:
        out.update(shot_tendencies(profile, fga, league_shots, a))
    return out


def _hidden(p, rng) -> dict[str, int]:
    st = p["stats"]
    clamp = lambda x: int(max(ATTR_MIN, min(ATTR_MAX, round(x))))  # noqa: E731
    g_prev = st["g"] if st else 0
    # Missing lots of games over ~1.1 seasons raises injury proneness (a rough signal).
    seasons_seen = 1.1 if p["experience"] >= 1 else 0.15
    avail = min(1.0, g_prev / (82 * seasons_seen)) if p["experience"] >= 1 else 0.8
    vet = min(p["experience"], 15)
    return {
        "consistency": clamp(rng.gauss(9 + vet / 5, 3)),
        "clutch": clamp(rng.gauss(10, 3.5)),
        "injury_proneness": clamp(6 + 10 * (1 - avail) + rng.gauss(0, 2)),
        "work_ethic": clamp(rng.gauss(11, 3)),
        "professionalism": clamp(rng.gauss(9 + vet / 4, 3)),
        "ambition": clamp(rng.gauss(11, 3.5)),
        "loyalty": clamp(rng.gauss(10, 3.5)),
    }


def _secondary(pos: str, h: int) -> list[str]:
    order = ["PG", "SG", "SF", "PF", "C"]
    i = order.index(pos)
    out = []
    if i > 0 and h < {"SG": 198, "SF": 203, "PF": 207, "C": 210}.get(pos, 0):
        out.append(order[i - 1])
    if i < 4 and h > {"PG": 190, "SG": 196, "SF": 202, "PF": 208}.get(pos, 999):
        out.append(order[i + 1])
    return out


def _league_file(year, season, today, teams, players, by_site) -> dict:
    out_teams = []
    for t in teams:
        roster = by_site[t["site"]]
        top = sorted((compute_ability(Attributes(**p["attributes"]), Position(p["position"])) for p in roster),
                     reverse=True)[:8]
        quality = statistics.fmean(top) if top else 90
        out_teams.append({
            "abbreviation": t["abbreviation"], "city": t["city"], "name": t["name"],
            "conference": t["conference"], "division": t["division"],
            "reputation": int(max(1, min(100, round(20 + (quality - 90) * 0.9 + BIG_MARKETS.get(t["site"], 0))))),
            "tactics": {"pace": int(max(1, min(20, round(10 + (t["pace"] - 99) / 1.2)))),
                        "three_point_rate": int(max(1, min(20, round(10 + (t["fg3a_rate"] - 0.42) / 0.015))))},
        })
    abbr = {t["site"]: t["abbreviation"] for t in teams}
    keep = ("first_name", "last_name", "birth_date", "nationality", "position", "secondary_positions",
            "height_cm", "wingspan_cm", "weight_kg", "jersey_number", "attributes", "potential_ability",
            "hidden", "tendencies")
    out_players = []
    history, drafted = (careers(year), drafts(year)) if WITH_HISTORY else ({}, {})
    for p in players:
        d = {k: p[k] for k in keep}
        d["team"] = abbr.get(p["_site"]) if p["_site"] else None
        d["contract"] = p["contract"] if p["_site"] else None
        d["years_experience"] = p["experience"]
        d["draft_year"] = season - p["experience"]       # rookies and sophomores: Rising Stars
        if p["_id"] in drafted:
            d["draft_year"], d["draft_pick"] = drafted[p["_id"]]
        elif history.get(p["_id"]):                      # undrafted: arrived in his first season
            d["draft_year"] = history[p["_id"]][0]["season"]
        if history.get(p["_id"]):
            d["career"] = history[p["_id"]]
        out_players.append(d)
    return {"format": "hoopsim-league", "version": 1,
            "name": f"NBA {season}-{str(year)[2:]}", "season": season, "start_date": today.isoformat(),
            "source": "Ratings derived from Basketball-Reference stats; personal use only.",
            "rules": RULES, "teams": out_teams, "players": out_players}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--year", type=int, default=2027, help="season's end year on the site (2027 = 2026-27)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--fetch", action="store_true", help="fetch missing pages first (about 5 minutes the first time)")
    ap.add_argument("--no-history", action="store_true", help="skip past seasons (careers, drafts, honours)")
    args = ap.parse_args()
    if "SSL_CERT_FILE" not in os.environ and Path("/root/.ccr/ca-bundle.crt").exists():
        os.environ["SSL_CERT_FILE"] = "/root/.ccr/ca-bundle.crt"
    if args.fetch:
        bf.fetch_all(args.year, history=not args.no_history)
    global WITH_HISTORY
    WITH_HISTORY = not args.no_history
    league = build(args.year)
    out = Path(args.out or ROOT / "leagues" / f"nba-{args.year - 1}-{str(args.year)[2:]}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(league, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out}: {len(league['teams'])} teams, {len(league['players'])} players")


if __name__ == "__main__":
    main()
