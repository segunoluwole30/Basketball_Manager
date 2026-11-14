"""Regular-season awards, announced when the regular season ends (before the playoffs).

Awards, all from regular-season box scores:

* **MVP**, **Defensive Player of the Year**, **Rookie of the Year**, **Sixth Man of
  the Year** and **Most Improved Player**: a simulated media vote (100 voters, NBA
  ballots: 10-7-5-3-1 points for MVP, 5-3-1 for the others), so each award has a
  winner and finalists with a share of the vote.
* **All-NBA** First, Second and Third Teams (positionless, as the NBA has been since
  2023-24), **All-Defensive** First and Second Teams and **All-Rookie** First and
  Second Teams, five players each, from the same kind of ballot (5-3-1 / 2-1 points).
* Finals MVP is named after the Finals, in ``season.finish_season``.

Clutch Player of the Year and Coach of the Year are left out: box scores carry no
clutch-time split and the game has no coaches.

**Eligibility** follows the NBA's 65-game rule, scaled to the season: a player needs
65/82 of the season's games with at least 20 minutes (scaled to the game's length),
and up to two games (scaled too) of 15-20 minutes also count. It applies to MVP,
DPOY, MIP, All-NBA and All-Defensive. Rookie of the Year, All-Rookie and Sixth Man are
exempt, as in the NBA, but Rookie of the Year and Sixth Man need half of the season's
games and All-Rookie a quarter. A team is short when too few players qualify (e.g. a
small league's rookie class).

**Scores** (per game unless said otherwise; "value" is Game Score plus 0.15 x
plus-minus, i.e. production with a nod to on-court impact):

* MVP: value + 20 x (team win% - 0.5), a bonus for being his team's best player and a
  little for availability: voters reward winning, as the NBA's do.
* All-NBA: value + 5 x (team win% - 0.5).
* DPOY / All-Defensive: steals, blocks and defensive rebounds, a defensive reputation
  (the scouts' defensive ratings), the team's defensive rating against the league's
  and its record; scaled by minutes, so a part-timer can't win it.
* ROY / All-Rookie: value + 4 x (team win% - 0.5).
* Sixth Man: value, for players who came off the bench in more games than they started.
* MIP: the jump in value and scoring from last season (20+ games then), damped for
  players who were already stars.

Voters see each score with some noise (their own judgement), so a close race can go
either way. The vote draws on its own random stream (seeded by league and season), so
it never changes the season's results.

Winners get a morale lift (single awards +6, a team selection +3). State lives in
``league.awards``; ``finish_season`` puts the honours on the career lines and files the
whole announcement in the season's history.
"""
from __future__ import annotations

import math
import random
from datetime import timedelta
from typing import Optional

from .models import League

SINGLE = [  # key, name, short
    ("mvp", "Most Valuable Player", "MVP"),
    ("dpoy", "Defensive Player of the Year", "DPOY"),
    ("roy", "Rookie of the Year", "ROY"),
    ("sixth_man", "Sixth Man of the Year", "6MOY"),
    ("mip", "Most Improved Player", "MIP"),
]
TEAMS = [  # key, name, number of teams
    ("all_nba", "All-NBA", 3),
    ("all_defense", "All-Defensive", 2),
    ("all_rookie", "All-Rookie", 2),
]
SINGLE_NAMES = {k: n for k, n, _ in SINGLE}
TEAM_NAMES = {k: n for k, n, _ in TEAMS}
TIER_NAMES = ["First Team", "Second Team", "Third Team"]

VOTERS = 100
BALLOT = {"mvp": [10, 7, 5, 3, 1]}
DEFAULT_BALLOT = [5, 3, 1]
TEAM_BALLOT = {3: [5, 3, 1], 2: [2, 1]}
VOTER_NOISE = 0.35            # in standard deviations of the leading candidates' scores
TEAM_SIZE = 5
MORALE_WIN, MORALE_TEAM = 6.0, 3.0

# The NBA's 65-game rule (2023-24 CBA), scaled to the season's length.
RULE_GAMES, NBA_GAMES = 65, 82
RULE_MINUTES, NEAR_MINUTES, NEAR_GAMES = 20, 15, 2
PARTICIPATION = 0.5           # ROY and Sixth Man: half the season's games
ROOKIE_TEAM_SHARE = 0.25      # All-Rookie: a quarter of them
RULED = ("mvp", "dpoy", "mip", "all_nba", "all_defense")


# --------------------------------------------------------------------------
# Eligibility
# --------------------------------------------------------------------------
def rules(league: League) -> dict:
    """The thresholds for this league's season length and game length."""
    g = league.rules.games_per_season
    scale = league.rules.quarter_minutes * 4 / 48
    return {"games": g, "required": max(1, math.ceil(g * RULE_GAMES / NBA_GAMES - 1e-9)),
            "min_minutes": round(RULE_MINUTES * scale, 1), "near_minutes": round(NEAR_MINUTES * scale, 1),
            "near_games": round(NEAR_GAMES * g / NBA_GAMES),
            "participation": max(1, math.ceil(g * PARTICIPATION - 1e-9))}


def counted_games(league: League, season: Optional[int] = None) -> dict[int, int]:
    """{player id: games that count toward the 65-game rule} from regular-season box scores."""
    season = league.season if season is None else season
    r = rules(league)
    full: dict[int, int] = {}
    near: dict[int, int] = {}
    for g in league.games.values():
        if g.season != season or g.playoff or not g.box_score:
            continue
        for box in (g.box_score.home, g.box_score.away):
            for line in box.lines:
                m = line.seconds / 60
                if m >= r["min_minutes"]:
                    full[line.player_id] = full.get(line.player_id, 0) + 1
                elif m >= r["near_minutes"]:
                    near[line.player_id] = near.get(line.player_id, 0) + 1
    return {pid: full.get(pid, 0) + min(near.get(pid, 0), r["near_games"]) for pid in set(full) | set(near)}


# --------------------------------------------------------------------------
# Scores
# --------------------------------------------------------------------------
def _value(t: dict) -> float:
    from .stats import game_score
    return game_score(t) + 0.15 * t.get("plus_minus", 0) / max(1, t["gp"])


def _context(league: League, totals: dict[int, dict]) -> dict:
    """Team records and defensive ratings, the league's defensive rating."""
    from .stats_tables import team_line, team_totals
    pct = {team.id: w / max(1, w + l) for team, w, l in league.standings()}
    tt = team_totals(league, league.season)
    drtg = {tid: team_line(t)["drtg"] for tid, t in tt.items() if t["poss"]}
    pts = sum(t["opp_pts"] for t in tt.values())
    poss = sum(t["poss"] for t in tt.values())
    return {"pct": pct, "drtg": drtg, "lg_drtg": 100 * pts / poss if poss else None}


def _defence(league: League, pid: int, t: dict, ctx: dict) -> float:
    from .stats import per_game
    a = league.players[pid].attributes
    rep = 0.5 * max(a.perimeter_defense, a.interior_defense) + 0.5 * (a.perimeter_defense + a.interior_defense) / 2
    box = 1.1 * per_game(t, "stl") + 1.1 * per_game(t, "blk") + 0.12 * per_game(t, "drb")
    minutes = min(1.0, per_game(t, "min") / 32)
    team = 0.0
    if ctx["lg_drtg"] and t["team_id"] in ctx["drtg"]:
        team = 0.2 * (ctx["lg_drtg"] - ctx["drtg"][t["team_id"]])
    return minutes * (box + 0.3 * (rep - 10)) + team + 1.5 * (ctx["pct"].get(t["team_id"], 0.5) - 0.5)


def _previous(league: League, pid: int, min_gp: int) -> Optional[dict]:
    prev = [c for c in league.players[pid].career
            if c["season"] == league.season - 1 and not c["playoffs"] and c["gp"] >= min_gp]
    if not prev:
        return None
    gp = sum(c["gp"] for c in prev)
    out = {k: sum(c.get(k, 0) for c in prev) for k in (
        "pts", "fgm", "fga", "fta", "ftm", "orb", "drb", "stl", "ast", "blk", "pf", "tov", "plus_minus")}
    out["gp"] = gp
    return out


def scores(league: League, totals: dict[int, dict]) -> dict[str, dict[int, float]]:
    """{award key: {eligible player id: score}} for every award and team."""
    from .stats import per_game
    r = rules(league)
    ctx = _context(league, totals)
    counted = counted_games(league)
    pct = ctx["pct"]
    G = r["games"]
    live = {pid: t for pid, t in totals.items() if pid in league.players}
    ruled = {pid for pid in live if counted.get(pid, 0) >= r["required"]}
    present = {pid for pid, t in live.items() if t["gp"] >= r["participation"]}
    rookies = {pid for pid in present if league.players[pid].draft_year == league.season}
    out: dict[str, dict[int, float]] = {}
    value = {pid: _value(t) for pid, t in live.items()}
    team_pct = {pid: pct.get(t["team_id"], 0.5) for pid, t in live.items()}
    best = {}                                          # each team's most valuable player
    for pid in ruled:
        tid = live[pid]["team_id"]
        if tid not in best or value[pid] > value[best[tid]]:
            best[tid] = pid
    leaders = set(best.values())
    out["mvp"] = {pid: value[pid] + 20 * (team_pct[pid] - 0.5) + 1.5 * (pid in leaders)
                  + 1.5 * live[pid]["gp"] / G for pid in ruled}
    out["all_nba"] = {pid: value[pid] + 5 * (team_pct[pid] - 0.5) for pid in ruled}
    out["dpoy"] = {pid: _defence(league, pid, live[pid], ctx) for pid in ruled}
    out["all_defense"] = dict(out["dpoy"])
    out["roy"] = {pid: value[pid] + 4 * (team_pct[pid] - 0.5) for pid in rookies}
    young = {pid for pid, t in live.items() if t["gp"] >= ROOKIE_TEAM_SHARE * G
             and league.players[pid].draft_year == league.season}
    out["all_rookie"] = {pid: value[pid] + 4 * (team_pct[pid] - 0.5) for pid in young}
    out["sixth_man"] = {pid: value[pid] + 2 * (team_pct[pid] - 0.5) for pid in present
                        if live[pid]["gs"] * 2 < live[pid]["gp"]}
    mip = {}
    for pid in ruled:
        prev = _previous(league, pid, max(1, round(0.25 * G)))
        if prev is None:
            continue
        before = _value(prev)
        jump = (value[pid] - before) + 0.3 * (per_game(live[pid], "pts") - prev["pts"] / prev["gp"])
        jump -= 0.4 * max(0.0, before - 14)
        if jump > 0:
            mip[pid] = jump
    out["mip"] = mip
    return out


# --------------------------------------------------------------------------
# The vote
# --------------------------------------------------------------------------
def _ballots(score: dict[int, float], points: list[int], rng: random.Random,
             pool: int = 12) -> list[dict]:
    """Every voter ranks the leading candidates by what he sees (score plus noise).
    Returns [{"id", "points", "first"}], most points first."""
    top = sorted(score, key=lambda pid: (-score[pid], pid))[:max(pool, len(points) + 4)]
    if not top:
        return []
    vals = [score[pid] for pid in top]
    mean = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals)) or 1.0
    got = {pid: 0 for pid in top}
    first = {pid: 0 for pid in top}
    for _ in range(VOTERS):
        seen = sorted(top, key=lambda pid: -(score[pid] / sd + rng.gauss(0, VOTER_NOISE)))
        for pid, pts in zip(seen, points):
            got[pid] += pts
        first[seen[0]] += 1
    order = sorted(top, key=lambda pid: (-got[pid], -score[pid], pid))
    return [{"id": pid, "points": got[pid], "first": first[pid]} for pid in order if got[pid] > 0]


def snapshot(league: League, pid: int, t: dict) -> dict:
    """What the announcement shows about a player (kept in the history for good)."""
    from .stats import per_game
    p = league.players[pid]
    team = league.teams.get(t["team_id"])
    return {"id": pid, "name": p.name, "pos": p.position.value, "team_id": t["team_id"],
            "team": team.abbreviation if team else "?", "gp": int(t["gp"]),
            "mpg": round(per_game(t, "min"), 1), "ppg": round(per_game(t, "pts"), 1),
            "rpg": round(per_game(t, "trb"), 1), "apg": round(per_game(t, "ast"), 1),
            "spg": round(per_game(t, "stl"), 1), "bpg": round(per_game(t, "blk"), 1)}


def compute(league: League, totals: Optional[dict[int, dict]] = None) -> dict:
    """The season's awards from regular-season totals (doesn't change the league)."""
    from .season import season_start
    from .stats import season_totals
    totals = season_totals(league) if totals is None else totals
    rng = random.Random(f"{league.name}|{league.season}|awards")
    sc = scores(league, totals)
    last_day = max((g.day for g in league.games.values() if g.season == league.season and not g.playoff),
                   default=0)
    out = {"season": league.season, "date": (season_start(league.season) + timedelta(days=last_day)).isoformat(),
           "rules": rules(league), "single": {}, "teams": {}, "seen": False}
    for key, _, _ in SINGLE:
        points = BALLOT.get(key, DEFAULT_BALLOT)
        res = _ballots(sc[key], points, rng)
        top = VOTERS * points[0]
        finalists = [{**snapshot(league, r["id"], totals[r["id"]]), "points": r["points"],
                      "share": round(r["points"] / top, 3), "first": r["first"]} for r in res[:3]]
        out["single"][key] = {"winner": finalists[0]["id"] if finalists else None, "finalists": finalists}
    for key, _, n in TEAMS:
        points = [pts for pts in TEAM_BALLOT[n] for _ in range(TEAM_SIZE)]
        res = _ballots(sc[key], points, rng, pool=TEAM_SIZE * n + 10)[:TEAM_SIZE * n]
        tiers = [res[i:i + TEAM_SIZE] for i in range(0, len(res), TEAM_SIZE)]
        out["teams"][key] = [[{**snapshot(league, r["id"], totals[r["id"]]), "points": r["points"]}
                              for r in tier] for tier in tiers]
    return out


def winners(data: dict) -> dict[str, Optional[int]]:
    """{award key: winner id} for the single awards."""
    return {key: data["single"].get(key, {}).get("winner") for key, _, _ in SINGLE}


def career_flags(data: dict) -> dict[int, dict]:
    """{player id: {"mvp": True, "all_nba": 1, ...}} for the season's career lines."""
    flags: dict[int, dict] = {}
    for key, pid in winners(data).items():
        if pid is not None:
            flags.setdefault(pid, {})[key] = True
    for key, tiers in data.get("teams", {}).items():
        for tier, players in enumerate(tiers, start=1):
            for row in players:
                flags.setdefault(row["id"], {})[key] = tier
    return flags


def current(league: League) -> Optional[dict]:
    """This season's announced awards, if any."""
    a = league.awards
    return a if a and a.get("season") == league.season else None


def announce(league: League) -> dict:
    """Name the season's awards once the regular season is over: store them on the
    league, lift the winners' morale and post the news. Idempotent."""
    from .season import regular_season_over
    have = current(league)
    if have is not None:
        return have
    if not regular_season_over(league):
        raise ValueError("the regular season isn't over")
    data = compute(league)
    league.awards = data
    for pid, flags in career_flags(data).items():
        p = league.players.get(pid)
        if p is None:
            continue
        lift = MORALE_WIN if any(v is True for v in flags.values()) else MORALE_TEAM
        p.morale = min(100.0, p.morale + lift)
    _post_news(league, data)
    return data


def ensure(league: League) -> Optional[dict]:
    """The season's awards, naming them now if the regular season is over and they
    haven't been (e.g. a save from before awards were announced at this point)."""
    from .season import regular_season_over
    have = current(league)
    if have is not None:
        return have
    if regular_season_over(league):
        return announce(league)
    return None


# --------------------------------------------------------------------------
# Text: news items, terminal
# --------------------------------------------------------------------------
def _label(row: dict) -> str:
    return f"{row['name']} ({row['team']})"


def _line(key: str, row: dict) -> str:
    if key in ("dpoy", "all_defense"):
        return f"{row['spg']} stl, {row['bpg']} blk, {row['rpg']} reb"
    return f"{row['ppg']} pts, {row['rpg']} reb, {row['apg']} ast"


def single_lines(key: str, entry: dict) -> list[str]:
    out = []
    for i, r in enumerate(entry["finalists"]):
        votes = f"{r['first']} first-place votes, " if key == "mvp" else ""
        out.append(f"{i + 1}. {_label(r)}: {_line(key, r)} ({votes}{round(100 * r['share'])}% of the vote)")
    return out


def team_lines(key: str, tiers: list) -> list[str]:
    return [f"{TIER_NAMES[i]}: " + ", ".join(_label(r) for r in tier) for i, tier in enumerate(tiers)]


def _post_news(league: League, data: dict) -> None:
    if league.user_team_id is None:
        return
    from .news import post
    me = league.user_team_id
    season = data["season"]
    mine = lambda r: r["team_id"] == me              # noqa: E731
    teams_body = []
    for key, name, _ in TEAMS:
        tiers = data["teams"].get(key, [])
        if tiers:
            teams_body.append(f"{name}")
            teams_body += ["  " + line for line in team_lines(key, tiers)]
    picked = [f"{r['name']} ({TEAM_NAMES[key]} {TIER_NAMES[i]})" for key, _, _ in TEAMS
              for i, tier in enumerate(data["teams"].get(key, [])) for r in tier if mine(r)]
    if picked:
        teams_body.insert(0, "Your players: " + "; ".join(picked) + ".")
    if teams_body:
        post(league, "awards", "All-NBA, All-Defensive and All-Rookie teams", teams_body, awards_season=season)
    for key, name, _ in reversed(SINGLE):              # the MVP last, so it tops the inbox
        entry = data["single"][key]
        if entry["winner"] is None:
            continue
        w = entry["finalists"][0]
        title = f"{name}: {w['name']}"
        body = []
        if mine(w):
            body.append(f"Congratulations! {w['name']} is the {name}.")
        elif any(mine(r) for r in entry["finalists"]):
            body.append("Your player was a finalist.")
        body += single_lines(key, entry)
        post(league, "awards", title, body, awards_season=season, player_id=w["id"])


def report(data: dict, user_team_id: Optional[int] = None) -> list[str]:
    """The announcement as terminal lines (your players marked "*")."""
    mark = lambda r: "*" if user_team_id is not None and r["team_id"] == user_team_id else ""  # noqa: E731
    out = []
    for key, name, _ in SINGLE:
        entry = data["single"].get(key)
        if not entry:
            continue
        if entry["winner"] is None:
            out.append(f"  {name:<30}-")
            continue
        w = entry["finalists"][0]
        out.append(f"  {name:<30}{_label(w)}, {round(100 * w['share'])}% of the vote{mark(w)}")
        rest = [f"{_label(r)} {round(100 * r['share'])}%{mark(r)}" for r in entry["finalists"][1:]]
        if rest:
            out.append(f"  {'':<30}finalists: " + ", ".join(rest))
    for key, name, _ in TEAMS:
        for i, tier in enumerate(data["teams"].get(key, [])):
            out.append(f"  {name + ' ' + TIER_NAMES[i]:<30}" + ", ".join(_label(r) + mark(r) for r in tier))
    return out
