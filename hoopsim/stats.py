"""Season stats, career lines, awards and morale."""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from .models import STAT_FIELDS, League, Player

COUNTING = STAT_FIELDS  # fgm fga tpm tpa ftm fta orb drb ast stl blk tov pf plus_minus


def season_totals(league: League, season: Optional[int] = None,
                  playoff: bool = False) -> dict[int, dict]:
    """Per-player totals from stored box scores: {player id: {gp, gs, seconds, pts, ...}}.

    ``team_id`` is the team the player most recently played for.
    """
    season = league.season if season is None else season
    totals: dict[int, dict] = defaultdict(lambda: defaultdict(float))
    games = sorted((g for g in league.games.values()
                    if g.season == season and g.playoff == playoff and g.box_score),
                   key=lambda g: (g.day, g.id))
    for g in games:
        for box in (g.box_score.home, g.box_score.away):
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
                t["team_id"] = box.team_id
    return {pid: dict(t) for pid, t in totals.items()}


def per_game(t: dict, key: str) -> float:
    if key == "min":
        return t["seconds"] / 60 / max(1, t["gp"])
    if key == "trb":
        return (t["orb"] + t["drb"]) / max(1, t["gp"])
    return t.get(key, 0) / max(1, t["gp"])


def game_score(t: dict) -> float:
    """Hollinger's Game Score per game: one-number box-score value."""
    gs = (t["pts"] + 0.4 * t["fgm"] - 0.7 * t["fga"] - 0.4 * (t["fta"] - t["ftm"])
          + 0.7 * t["orb"] + 0.3 * t["drb"] + t["stl"] + 0.7 * t["ast"] + 0.7 * t["blk"]
          - 0.4 * t["pf"] - t["tov"])
    return gs / max(1, t["gp"])


def career_line(t: dict, season: int, playoff: bool, league: League) -> dict:
    team = league.teams.get(t["team_id"])
    line = {"season": season, "playoffs": playoff, "team_id": t["team_id"],
            "team": team.abbreviation if team else "?",
            "gp": int(t["gp"]), "gs": int(t["gs"]), "min": round(t["seconds"] / 60, 1),
            "pts": int(t["pts"])}
    line.update({f: int(t[f]) for f in COUNTING})
    return line


def totals_from_careers(league: League, season: int, playoff: bool = False) -> dict[int, dict]:
    """Season totals rebuilt from stored career lines (box scores are pruned after a season)."""
    out = {}
    for p in league.players.values():
        for c in p.career:
            if c["season"] == season and c["playoffs"] == playoff:
                out[p.id] = {**c, "seconds": c["min"] * 60}
    return out


def career_per_game(line: dict) -> dict:
    gp = max(1, line["gp"])
    return {"ppg": line["pts"] / gp, "rpg": (line["orb"] + line["drb"]) / gp,
            "apg": line["ast"] / gp, "mpg": line["min"] / gp}


# --------------------------------------------------------------------------
# Awards
# --------------------------------------------------------------------------
def _team_win_pct(league: League) -> dict[int, float]:
    return {team.id: w / max(1, w + l) for team, w, l in league.standings()}


def awards(league: League, totals: dict[int, dict]) -> dict[str, Optional[int]]:
    """MVP, DPOY, ROY, 6MOY and MIP winners (player ids) for the current season.
    The full vote, finalists and All-NBA teams are in ``awards.compute``."""
    from .awards import compute, winners
    return winners(compute(league, totals))


def leaders(league: League, totals: dict[int, dict], key: str, n: int = 3) -> list[tuple[int, float]]:
    qualified = [pid for pid, t in totals.items() if t["gp"] >= 0.6 * league.rules.games_per_season]
    ranked = sorted(qualified, key=lambda pid: per_game(totals[pid], key), reverse=True)[:n]
    return [(pid, per_game(totals[pid], key)) for pid in ranked]


# --------------------------------------------------------------------------
# Morale
# --------------------------------------------------------------------------
def _expected_minutes(rank: int) -> float:
    """Minutes a player of this ability rank on his team expects."""
    return 32 if rank < 5 else 22 if rank < 8 else 14 if rank < 10 else 6


def update_morale(league: League, totals: dict[int, dict], rate: float = 0.2) -> None:
    """Drift morale toward a target set by playing time, winning and contract security."""
    pct = _team_win_pct(league)
    for team in league.teams.values():
        roster = league.roster(team.id)  # sorted by ability
        for rank, p in enumerate(roster):
            t = totals.get(p.id)
            mpg = per_game(t, "min") if t else 0.0
            playing_time = 0.0 if p.injury else max(-15.0, min(10.0, (mpg - _expected_minutes(rank)) * 1.2))
            winning = (pct.get(team.id, 0.5) - 0.5) * 40 * (0.5 + p.hidden.ambition / 20)
            target = 55 + playing_time + winning
            p.morale = max(0.0, min(100.0, p.morale + rate * (target - p.morale)))
    for p in league.free_agents():
        p.morale = max(0.0, min(100.0, p.morale + rate * (40 - p.morale)))


def player_label(league: League, pid: Optional[int]) -> str:
    if pid is None:
        return "-"
    p: Player = league.players[pid]
    team = league.teams.get(p.team_id) if p.team_id else None
    return f"{p.name} ({team.abbreviation if team else 'FA'})"
