"""League files: start a game from your own teams and players (JSON).

A league file lists teams and players with their ratings; hoopsim fills in the
rest (ids, lineups, the schedule). ``tools/build_nba_league.py`` writes one for the
real NBA from public stats, for personal use; you can also write or edit one by hand.

Format (``"format": "hoopsim-league", "version": 1``)::

    {
      "name": "My League", "season": 2026,            # season 2026 = 2026-27
      "start_date": "2026-10-20",                     # optional
      "rules": {"salary_cap": 165000000, ...},        # optional, any LeagueRules field
      "teams": [{"abbreviation": "BOS", "city": "Boston", "name": "Celtics",
                 "conference": "East", "division": "Atlantic",
                 "reputation": 60, "tactics": {"pace": 10}}, ...],
      "players": [{"first_name": "A", "last_name": "B", "team": "BOS",   # null = free agent
                   "position": "PG", "birth_date": "1998-01-28",
                   "height_cm": 185, "weight_kg": 88,
                   "attributes": {"three_point": 17, ...},             # 1-20, missing = 10
                   "potential_ability": 140,                         # optional
                   "contract": {"salary": 30000000, "expires_season": 2028},
                   "draft_year": 2019, "draft_pick": 3,                  # optional
                   "career": [{"season": 2019, "playoffs": false, "team": "BOS",
                               "gp": 70, "pts": 1200, ..., "all_star": true}]}, ...]
    }

Everything but names, team abbreviations and positions is optional.
"""
from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path
from typing import Union

from .generate import NBA_DIVISIONS, generate_contract, generate_schedule
from .lineup import auto_lineup
from .models import (ABILITY_MAX, ATTR_MAX, ATTR_MIN, Attributes, Contract, ContractOption,
                     HiddenAttributes, League, LeagueRules, Physique, Player, Position, Tactics,
                     Team, Tendencies, compute_ability)

FORMAT = "hoopsim-league"


class LeagueFileError(ValueError):
    pass


def _ratings(cls, d: dict | None, where: str):
    d = dict(d or {})
    unknown = set(d) - set(cls.names())
    if unknown:
        raise LeagueFileError(f"{where}: unknown ratings {sorted(unknown)}")
    for k, v in d.items():
        if not isinstance(v, (int, float)) or not ATTR_MIN <= v <= ATTR_MAX:
            raise LeagueFileError(f"{where}: {k} must be {ATTR_MIN}-{ATTR_MAX}, got {v!r}")
    return cls(**{k: int(round(v)) for k, v in d.items()})


def _position(v, where) -> Position:
    try:
        return Position(str(v).upper())
    except ValueError:
        raise LeagueFileError(f"{where}: position must be PG, SG, SF, PF or C, got {v!r}") from None


CAREER_NUMBERS = ("gp", "gs", "min", "pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta",
                  "orb", "drb", "ast", "stl", "blk", "tov", "pf", "plus_minus")
CAREER_HONOURS = ("all_star", "asg_mvp", "three_point_champion", "rising_stars_mvp", "mvp", "dpoy", "roy",
                  "sixth_man", "mip", "clutch_poy", "finals_mvp", "all_nba", "all_defense")


def _career(lines, by_abbr: dict, season: int, where: str) -> list[dict]:
    """Past seasons as the game stores them (one line per season, regular season or playoffs)."""
    out = []
    for c in lines or []:
        try:
            s = int(c["season"])
        except (KeyError, TypeError, ValueError):
            raise LeagueFileError(f"{where}: every career line needs a season") from None
        if s >= season:
            raise LeagueFileError(f"{where}: career seasons must be before {season}")
        team = str(c.get("team", "?"))
        line = {"season": s, "playoffs": bool(c.get("playoffs", False)), "team": team,
                "team_id": by_abbr.get(str(c.get("franchise", team)).upper()),
                "age": c.get("age"), "ca": c.get("ca")}
        for k in CAREER_NUMBERS:
            v = c.get(k, 0)
            if not isinstance(v, (int, float)) or v < 0 and k != "plus_minus":
                raise LeagueFileError(f"{where}: career {k} must be a number")
            line[k] = v
        line.update({k: c[k] for k in CAREER_HONOURS if c.get(k)})
        out.append(line)
    return sorted(out, key=lambda c: (c["season"], c["playoffs"]))


def load_league_file(source: Union[str, Path, dict], *, seed: int = 0) -> League:
    """A new league from a league file (path or already-parsed dict)."""
    data = source if isinstance(source, dict) else json.loads(Path(source).read_text(encoding="utf-8"))
    if data.get("format") != FORMAT:
        raise LeagueFileError(f'not a league file (expected "format": "{FORMAT}")')
    if int(data.get("version", 1)) != 1:
        raise LeagueFileError(f"unsupported league file version {data.get('version')}")
    rng = random.Random(seed)
    season = int(data.get("season", 2026))
    start = date.fromisoformat(data["start_date"]) if data.get("start_date") else date(season, 10, 20)
    rules = LeagueRules()
    if data.get("rules"):
        known = set(rules.to_dict())
        bad = set(data["rules"]) - known
        if bad:
            raise LeagueFileError(f"unknown rules {sorted(bad)}")
        rules = LeagueRules.from_dict({**rules.to_dict(), **data["rules"]})
    league = League(name=str(data.get("name", "Custom League")), season=season, current_date=start,
                    rules=rules, info={"source": data.get("source") or "league file", "edited": []})

    teams = data.get("teams") or []
    if len(teams) < 2:
        raise LeagueFileError("a league needs at least two teams")
    by_abbr: dict[str, int] = {}
    for i, t in enumerate(teams, 1):
        where = f"team {i}"
        abbr = str(t.get("abbreviation", "")).upper()
        if not abbr or abbr in by_abbr:
            raise LeagueFileError(f"{where}: missing or duplicate abbreviation {abbr!r}")
        conf = t.get("conference") or ("East" if i <= (len(teams) + 1) // 2 else "West")
        if conf not in NBA_DIVISIONS:
            raise LeagueFileError(f"{where}: conference must be East or West")
        tac = {**Tactics().to_dict(), **(t.get("tactics") or {})}
        league.add_team(Team(
            id=i, city=str(t.get("city", abbr)), name=str(t.get("name", abbr)), abbreviation=abbr,
            conference=conf, division=str(t.get("division") or NBA_DIVISIONS[conf][0]),
            tactics=Tactics.from_dict(tac),
            reputation=int(max(1, min(100, t.get("reputation", 50)))),
            budget=int(t.get("budget", round(rng.uniform(5, 25) * 1_000_000, -5))),
            arena_capacity=int(t.get("arena_capacity", 18000)),
        ))
        by_abbr[abbr] = i

    for j, p in enumerate(data.get("players") or [], 1):
        where = f"player {j} ({p.get('first_name', '')} {p.get('last_name', '')})".strip()
        if not p.get("last_name") and not p.get("first_name"):
            raise LeagueFileError(f"player {j}: needs a name")
        pos = _position(p.get("position"), where)
        team = p.get("team")
        if team is not None and str(team).upper() not in by_abbr:
            raise LeagueFileError(f"{where}: unknown team {team!r}")
        attrs = _ratings(Attributes, p.get("attributes"), where)
        ca = compute_ability(attrs, pos)
        pa = int(p.get("potential_ability", ca))
        h = int(p.get("height_cm", {"PG": 190, "SG": 196, "SF": 201, "PF": 206, "C": 211}[pos.value]))
        player = Player(
            id=league.next_player_id(),
            first_name=str(p.get("first_name", "")), last_name=str(p.get("last_name") or p["first_name"]),
            birth_date=date.fromisoformat(p.get("birth_date") or f"{season - 25}-06-01"),
            nationality=str(p.get("nationality", "USA")), position=pos,
            secondary_positions=[_position(x, where) for x in p.get("secondary_positions", [])],
            physique=Physique(h, int(p.get("wingspan_cm", h + 8)), int(p.get("weight_kg", 100))),
            attributes=attrs,
            hidden=_ratings(HiddenAttributes, p.get("hidden"), where),
            tendencies=_ratings(Tendencies, p["tendencies"], where) if p.get("tendencies") else None,
            potential_ability=max(ca, min(ABILITY_MAX, pa)),
            jersey_number=p.get("jersey_number"),
            draft_year=p.get("draft_year"), draft_pick=p.get("draft_pick"),
            career=_career(p.get("career"), by_abbr, season, where),
        )
        league.add_player(player)
        if team is not None:
            player.team_id = by_abbr[str(team).upper()]
            c = p.get("contract")
            if c:
                opt = c.get("option")
                player.contract = Contract(int(c["salary"]), int(c["expires_season"]),
                                           ContractOption(opt) if opt else None, bool(c.get("rookie_scale")))
            else:
                player.contract = generate_contract(player, league, rng)

    for tid in league.teams:
        taken: set[int] = set()
        for p in league.roster(tid):             # two players with one number (trades): next free one
            if p.jersey_number is not None and p.jersey_number in taken:
                p.jersey_number = next(n for n in range(100) if n not in taken)
            if p.jersey_number is not None:
                taken.add(p.jersey_number)
        n = sum(1 for p in league.players.values() if p.team_id == tid)
        if n < 5:
            raise LeagueFileError(f"{league.teams[tid].abbreviation} has {n} players; a team needs at least 5")
        auto_lineup(league, tid)
    from .systems import DEFENSE, OFFENSE, apply, assign_ai_systems
    assign_ai_systems(league, random.Random(f"systems|{seed}"),
                      keep_sliders=frozenset(i for i, t in enumerate(teams, 1) if t.get("tactics")))
    for i, t in enumerate(teams, 1):                 # a file can name a team's systems
        o, d = t.get("offense_system"), t.get("defense_system")
        if o is not None and o not in OFFENSE or d is not None and d not in DEFENSE:
            raise LeagueFileError(f"team {i}: unknown coaching system ({o}, {d})")
        if o or d:
            apply(league.teams[i].tactics, offense=o, defense=d)
    for p in league.players.values():
        if p.age(start) <= 20 and p.draft_year is None:
            p.draft_year = season
    generate_schedule(league, rng)
    return league


def league_files(folder: Union[str, Path]) -> list[dict]:
    """League files in a folder: [{"file", "name", "teams", "players"}]."""
    out = []
    for f in sorted(Path(folder).glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("format") == FORMAT:
            out.append({"file": f.name, "name": d.get("name", f.stem), "teams": len(d.get("teams", [])),
                        "players": len(d.get("players", []))})
    return out
