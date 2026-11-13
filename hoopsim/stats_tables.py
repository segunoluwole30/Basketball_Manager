"""Read-only stat tables for the web app: player lines with advanced numbers,
team four-factor tables and game logs, all computed from stored box scores.

Nothing here changes the league. Box scores are only kept for the season being
played (``season.finish_season`` prunes them), so past seasons' player lines
come from the career lines stored on each player and have no team context.

Formulas (all from box scores):

* possessions, per team-game: ``FGA + 0.44*FTA - ORB + TOV``; a game's
  possessions are the average of the two teams' estimates, so both teams in a
  game share one count and league-wide offensive and defensive ratings agree.
* pace: possessions per 48 minutes. Ratings: points per 100 possessions.
* eFG% ``(FGM + 0.5*3PM) / FGA``; TOV% ``TOV / (FGA + 0.44*FTA + TOV)``;
  ORB% ``ORB / (ORB + opponent DRB)``; FT rate ``FTA / FGA``; 3PA rate ``3PA / FGA``.
* TS% ``PTS / (2 * (FGA + 0.44*FTA))``.
* USG% (Basketball-Reference's box-score estimate): the share of his team's
  plays (FGA + 0.44*FTA + TOV) he used while on the floor, from team totals in
  the games he played. FGA share: his FGA over his team's FGA in those games.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from .models import League
from .stats import COUNTING, totals_from_careers

SHOT_FIELDS = ("fgm", "fga", "tpm", "tpa", "ftm", "fta", "orb", "drb", "ast", "stl", "blk", "tov", "pf")


def _games(league: League, season: int, playoff: bool) -> list:
    play_in = {gid for s in league.playoffs if s.play_in for gid in s.game_ids}    # not playoff stats
    return sorted((g for g in league.games.values()
                   if g.season == season and g.playoff == playoff and g.box_score and g.result
                   and g.id not in play_in),
                  key=lambda g: (g.day, g.id))


def has_box_scores(league: League, season: int, playoff: bool) -> bool:
    return any(g.box_score for g in league.games.values() if g.season == season and g.playoff == playoff)


def _box_totals(box) -> dict:
    t = {f: 0 for f in SHOT_FIELDS}
    seconds = 0.0
    for line in box.lines:
        seconds += line.seconds
        for f in SHOT_FIELDS:
            t[f] += getattr(line, f)
    t["pts"] = 2 * t["fgm"] + t["tpm"] + t["ftm"]
    t["seconds"] = seconds
    return t


def possessions_estimate(t: dict) -> float:
    """One team's possessions in one game, from its box-score totals."""
    return t["fga"] + 0.44 * t["fta"] - t["orb"] + t["tov"]


# --------------------------------------------------------------------------
# Players
# --------------------------------------------------------------------------
def player_totals(league: League, season: int, playoff: bool = False) -> dict[int, dict]:
    """{player id: totals}, with team context (``tm_*``) when box scores exist.

    Falls back to stored career lines (no team context) for a finished season.
    ``team_id`` is the team he most recently played for.
    """
    games = _games(league, season, playoff)
    if not games:
        out = {}
        for pid, c in totals_from_careers(league, season, playoff).items():
            out[pid] = {**{f: c[f] for f in COUNTING}, "gp": c["gp"], "gs": c["gs"],
                        "seconds": c["seconds"], "pts": c["pts"], "team_id": c["team_id"],
                        "context": False}
        return out
    totals: dict[int, dict] = defaultdict(lambda: defaultdict(float))
    for g in games:
        for box in (g.box_score.home, g.box_score.away):
            team = _box_totals(box)
            for line in box.lines:
                if line.seconds <= 0:
                    continue
                t = totals[line.player_id]
                t["gp"] += 1
                t["gs"] += line.starter
                t["seconds"] += line.seconds
                t["pts"] += line.pts
                for f in COUNTING:
                    t[f] += getattr(line, f)
                t["tm_fga"] += team["fga"]
                t["tm_fta"] += team["fta"]
                t["tm_tov"] += team["tov"]
                t["tm_seconds"] += team["seconds"]
                t["team_id"] = box.team_id
    out = {}
    for pid, t in totals.items():
        d = dict(t)
        d["team_id"] = int(d["team_id"])
        d["context"] = True
        out[pid] = d
    return out


def _div(a: float, b: float) -> Optional[float]:
    return a / b if b else None


def player_line(t: dict) -> dict:
    """Per-game, shooting and advanced numbers from a totals dict (unrounded)."""
    gp = max(1, t["gp"])
    minutes = t["seconds"] / 60
    trb = t["orb"] + t["drb"]
    line = {
        "gp": int(t["gp"]), "gs": int(t["gs"]), "min": minutes, "mpg": minutes / gp,
        "pts": t["pts"] / gp, "trb": trb / gp, "orb": t["orb"] / gp, "drb": t["drb"] / gp,
        "ast": t["ast"] / gp, "stl": t["stl"] / gp, "blk": t["blk"] / gp, "tov": t["tov"] / gp,
        "pf": t["pf"] / gp, "tpm": t["tpm"] / gp,
        "fga_pg": t["fga"] / gp, "tpa_pg": t["tpa"] / gp, "fta_pg": t["fta"] / gp,
        "fg_pct": _div(t["fgm"], t["fga"]), "tp_pct": _div(t["tpm"], t["tpa"]),
        "ft_pct": _div(t["ftm"], t["fta"]),
        "efg": _div(t["fgm"] + 0.5 * t["tpm"], t["fga"]),
        "ts": _div(t["pts"], 2 * (t["fga"] + 0.44 * t["fta"])),
        "pts36": _div(36 * t["pts"], minutes), "trb36": _div(36 * trb, minutes),
        "ast36": _div(36 * t["ast"], minutes),
        "usg": None, "fga_share": None,
    }
    if t.get("context") and t["seconds"] > 0:
        team_plays = t["tm_fga"] + 0.44 * t["tm_fta"] + t["tm_tov"]
        mine = t["fga"] + 0.44 * t["fta"] + t["tov"]
        if team_plays:
            line["usg"] = mine * (t["tm_seconds"] / 5) / (t["seconds"] * team_plays)
        line["fga_share"] = _div(t["fga"], t["tm_fga"])
    return line


# Leader categories: key, label, kind ("count" per game, "pct" 0-1, "rate" per 36),
# and the extra qualifier beyond games played: (line key, per-game minimum, text).
LEADER_CATEGORIES = (
    ("pts", "Points", "count", None),
    ("trb", "Rebounds", "count", None),
    ("ast", "Assists", "count", None),
    ("stl", "Steals", "count", None),
    ("blk", "Blocks", "count", None),
    ("fg_pct", "FG%", "pct", ("fga_pg", 5.0, "5 FGA per game")),
    ("tp_pct", "3P%", "pct", ("tpa_pg", 2.0, "2 3PA per game")),
    ("ft_pct", "FT%", "pct", ("fta_pg", 2.0, "2 FTA per game")),
    ("ts", "True shooting %", "pct", ("fga_pg", 5.0, "5 FGA per game")),
    ("usg", "Usage %", "pct", ("mpg", 15.0, "15 minutes per game")),
    ("fga_share", "Share of team FGA", "pct", ("mpg", 15.0, "15 minutes per game")),
    ("pts36", "Points per 36", "rate", ("mpg", 15.0, "15 minutes per game")),
    ("trb36", "Rebounds per 36", "rate", ("mpg", 15.0, "15 minutes per game")),
    ("ast36", "Assists per 36", "rate", ("mpg", 15.0, "15 minutes per game")),
)


def min_games(league: League, season: int, playoff: bool, totals: dict[int, dict]) -> int:
    """Games needed to qualify: 60% of the most any team has played in the
    regular season; in the playoffs, one full series' worth (4) or fewer if
    nobody has played that many yet."""
    most = 0
    counts: dict[int, int] = defaultdict(int)
    for g in league.games.values():
        if g.season == season and g.playoff == playoff and g.played:
            counts[g.home_team_id] += 1
            counts[g.away_team_id] += 1
    most = max(counts.values(), default=0)
    if not most:                                        # a past season from career lines
        most = max((t["gp"] for t in totals.values()), default=0)
    if playoff:
        return max(1, min(4, most))
    return max(1, round(0.6 * most))


# --------------------------------------------------------------------------
# Teams
# --------------------------------------------------------------------------
def team_totals(league: League, season: int, playoff: bool = False) -> dict[int, dict]:
    """{team id: raw season totals for the team and its opponents} from box scores."""
    out: dict[int, dict] = {}
    for g in _games(league, season, playoff):
        home, away = _box_totals(g.box_score.home), _box_totals(g.box_score.away)
        poss = (possessions_estimate(home) + possessions_estimate(away)) / 2
        for box, mine, theirs in ((g.box_score.home, home, away), (g.box_score.away, away, home)):
            t = out.setdefault(box.team_id, {"gp": 0, "w": 0, "l": 0, "poss": 0.0, "minutes": 0.0,
                                             **{f: 0 for f in SHOT_FIELDS}, "pts": 0,
                                             **{f"opp_{f}": 0 for f in SHOT_FIELDS}, "opp_pts": 0})
            t["gp"] += 1
            won = mine["pts"] > theirs["pts"]
            t["w"] += won
            t["l"] += not won
            t["poss"] += poss
            t["minutes"] += mine["seconds"] / 60 / 5
            for f in SHOT_FIELDS + ("pts",):
                t[f] += mine[f]
                t[f"opp_{f}"] += theirs[f]
    return out


def team_line(t: dict) -> dict:
    """Per-game and four-factor numbers for one team (unrounded)."""
    gp = max(1, t["gp"])
    return {
        "gp": t["gp"], "w": t["w"], "l": t["l"],
        "ppg": t["pts"] / gp, "opp_ppg": t["opp_pts"] / gp, "diff": (t["pts"] - t["opp_pts"]) / gp,
        "poss": t["poss"], "pace": _div(48 * t["poss"], t["minutes"]),
        "ortg": _div(100 * t["pts"], t["poss"]), "drtg": _div(100 * t["opp_pts"], t["poss"]),
        "net": _div(100 * (t["pts"] - t["opp_pts"]), t["poss"]),
        "efg": _div(t["fgm"] + 0.5 * t["tpm"], t["fga"]),
        "tov_pct": _div(t["tov"], t["fga"] + 0.44 * t["fta"] + t["tov"]),
        "orb_pct": _div(t["orb"], t["orb"] + t["opp_drb"]),
        "ft_rate": _div(t["fta"], t["fga"]),
        "tpa_rate": _div(t["tpa"], t["fga"]),
        "opp_efg": _div(t["opp_fgm"] + 0.5 * t["opp_tpm"], t["opp_fga"]),
        "opp_tov_pct": _div(t["opp_tov"], t["opp_fga"] + 0.44 * t["opp_fta"] + t["opp_tov"]),
        "drb_pct": _div(t["drb"], t["drb"] + t["opp_orb"]),
        "opp_ft_rate": _div(t["opp_fta"], t["opp_fga"]),
    }
