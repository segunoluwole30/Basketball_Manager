"""The in-game editor: change any player's ratings, details and contract, or a
team's name, in the middle of a save (like Football Manager's editor).

Edits are checked against the same limits the game itself uses (ratings 1-20,
ability 1-200, a real position, roster limits), so an edited league is still a
legal one. Each edit is noted in ``league.info["edited"]``: a save shows that the
editor was used.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from .lineup import auto_lineup
from .models import (ABILITY_MAX, ABILITY_MIN, ATTR_MAX, ATTR_MIN, Attributes, Contract,
                     ContractOption, HiddenAttributes, League, Player, Position, Tendencies)

LOG_KEPT = 200


class EditError(ValueError):
    pass


def _note(league: League, what: str) -> None:
    log = league.info.setdefault("edited", [])
    log.append({"date": league.current_date.isoformat(), "season": league.season, "what": what})
    del log[:-LOG_KEPT]


def _int(v: Any, lo: int, hi: int, name: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v):
        raise EditError(f"{name} must be a whole number")
    if not lo <= v <= hi:
        raise EditError(f"{name} must be {lo}-{hi}")
    return int(v)


def _block(current, cls, changes: dict, label: str):
    unknown = set(changes) - set(cls.names())
    if unknown:
        raise EditError(f"unknown {label}: {', '.join(sorted(unknown))}")
    values = current.to_dict()
    for k, v in changes.items():
        values[k] = _int(v, ATTR_MIN, ATTR_MAX, k.replace("_", " "))
    return cls(**values)


# The 3D viewer's player looks (viewer.html lookOf): the values it understands.
LOOK_HAIR = ("short", "fade", "bald", "afro", "locs", "flattop", "bun")
LOOK_SIDES = ("L", "R", "both")                  # arm sleeve, leg sleeves, wristbands (or None)
LOOK_COLOURS = ("white", "black", "team")        # or "#rrggbb"
LOOK_KEYS = ("hair", "hairColor", "beard", "band", "sleeve", "leg", "wrist", "gear", "shoes", "face")
LOOK_FACES = 3                                   # face: this many weights, 0-1 each: how far toward each of the
                                                 # model's face shapes (the viewer's face_a, face_b, face_c)
_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def _colour(v: Any, name: str) -> str:
    if isinstance(v, str):
        v = v.strip()
        if v in LOOK_COLOURS:
            return v
        if _HEX.fullmatch(v):
            return v.lower()
    raise EditError(f"{name} must be white, black, team or a #rrggbb colour")


def clean_look(look: Any) -> Optional[dict]:
    """A player's 3D look, checked: None (or {}) means "pick it from his name"; otherwise a
    dict of any of LOOK_KEYS (the parts left out are still picked from his name)."""
    if look is None:
        return None
    if not isinstance(look, dict):
        raise EditError("look must be an object (or null for the default)")
    unknown = set(look) - set(LOOK_KEYS)
    if unknown:
        raise EditError(f"unknown look parts: {', '.join(sorted(map(str, unknown)))}")
    out: dict[str, Any] = {}
    for k in LOOK_KEYS:
        if k not in look:
            continue
        v = look[k]
        if k == "hair":
            if v not in LOOK_HAIR:
                raise EditError(f"hair must be one of {', '.join(LOOK_HAIR)}")
        elif k == "beard":
            if not isinstance(v, bool):
                raise EditError("beard must be true or false")
        elif k in ("sleeve", "leg", "wrist"):
            if v is not None and v not in LOOK_SIDES:
                raise EditError(f"{k} must be L, R, both or none")
        elif k == "band":
            v = None if v is None else _colour(v, "headband")
        elif k == "face":
            if (not isinstance(v, (list, tuple)) or len(v) != LOOK_FACES
                    or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not 0 <= x <= 1 for x in v)):
                raise EditError(f"face must be {LOOK_FACES} numbers from 0 to 1")     # (NaN fails the range test too)
            v = [round(float(x), 2) for x in v]
        else:
            v = _colour(v, {"hairColor": "hair colour", "gear": "gear colour", "shoes": "shoe colour"}[k])
        out[k] = v
    return out or None


def player_list(league: League, team_id: Optional[int] = None, query: str = "",
                free_agents: bool = False, limit: int = 60) -> list[dict]:
    """Players to pick from in the editor: a team, the free agents, or a name search."""
    q = query.strip().lower()
    out = []
    for p in league.players.values():
        if p.retired:
            continue
        if q:
            if q not in p.name.lower():
                continue
        elif free_agents:
            if p.team_id is not None:
                continue
        elif p.team_id != team_id:
            continue
        out.append(p)
    out.sort(key=lambda p: -p.current_ability)
    return [{"id": p.id, "name": p.name, "pos": p.position.value, "age": p.age(league.current_date),
             "ca": p.current_ability, "pa": p.potential_ability, "team_id": p.team_id,
             "team": league.teams[p.team_id].abbreviation if p.team_id else None} for p in out[:limit]]


def player_sheet(league: League, player: Player) -> dict:
    """Everything the editor can change, with the true values (no scouting fog)."""
    c = player.contract
    return {
        "id": player.id, "first_name": player.first_name, "last_name": player.last_name,
        "team_id": player.team_id, "birth_date": player.birth_date.isoformat(),
        "age": player.age(league.current_date), "nationality": player.nationality,
        "position": player.position.value, "secondary_positions": [p.value for p in player.secondary_positions],
        "height_cm": player.physique.height_cm, "wingspan_cm": player.physique.wingspan_cm,
        "weight_kg": player.physique.weight_kg, "jersey_number": player.jersey_number,
        "attributes": player.attributes.to_dict(), "hidden": player.hidden.to_dict(),
        "tendencies": player.tendencies.to_dict(),
        "current_ability": player.current_ability, "potential_ability": player.potential_ability,
        "morale": round(player.morale), "injury": player.injury.to_dict() if player.injury else None,
        "contract": None if c is None else {"salary": c.salary, "expires_season": c.expires_season,
                                             "option": c.option.value if c.option else None},
        "look": dict(player.look) if player.look else None,
        "limits": {"rating": [ATTR_MIN, ATTR_MAX], "ability": [ABILITY_MIN, ABILITY_MAX],
                   "positions": [p.value for p in Position],
                   "salary": [0, int(league.rules.salary_cap * league.rules.max_salary_cap_fraction)],
                   "expires_season": [league.season, league.season + 6],
                   "look": {"hair": list(LOOK_HAIR), "sides": list(LOOK_SIDES), "colours": list(LOOK_COLOURS),
                            "faces": LOOK_FACES}},
    }


def edit_player(league: League, player_id: int, changes: dict) -> list[str]:
    """Apply ``changes`` (any keys of ``player_sheet``) all at once, or none if one is
    invalid. Returns what changed, in words."""
    p = league.players.get(int(player_id))
    if p is None or p.retired:
        raise EditError("no such player")
    allowed = {"first_name", "last_name", "birth_date", "nationality", "position", "secondary_positions",
               "height_cm", "wingspan_cm", "weight_kg", "jersey_number", "attributes", "hidden",
               "tendencies", "potential_ability", "morale", "contract", "heal", "team_id", "look"}
    unknown = set(changes) - allowed
    if unknown:
        raise EditError(f"can't edit {', '.join(sorted(unknown))}")
    new: dict[str, Any] = {}
    done: list[str] = []
    for key in ("first_name", "last_name", "nationality"):
        if key in changes:
            v = str(changes[key]).strip()
            if not v or len(v) > 40:
                raise EditError(f"{key.replace('_', ' ')} must be 1-40 characters")
            new[key] = v
    if "birth_date" in changes:
        try:
            born = date.fromisoformat(str(changes["birth_date"]))
        except ValueError:
            raise EditError("birth date must be YYYY-MM-DD") from None
        age = league.current_date.year - born.year
        if not 15 <= age <= 50:
            raise EditError("age must be 15-50")
        new["birth_date"] = born
    pos = p.position
    if "position" in changes:
        try:
            pos = new["position"] = Position(str(changes["position"]).upper())
        except ValueError:
            raise EditError("position must be PG, SG, SF, PF or C") from None
    if "secondary_positions" in changes:
        try:
            sec = [Position(str(x).upper()) for x in changes["secondary_positions"]]
        except (ValueError, TypeError):
            raise EditError("secondary positions must be PG, SG, SF, PF or C") from None
        new["secondary_positions"] = [x for x in dict.fromkeys(sec) if x != pos]
    phys = {}
    for key, lo, hi in (("height_cm", 160, 235), ("wingspan_cm", 150, 250), ("weight_kg", 60, 160)):
        if key in changes:
            phys[key] = _int(changes[key], lo, hi, key.split("_")[0])
    if "jersey_number" in changes:
        n = changes["jersey_number"]
        n = None if n in (None, "") else _int(n, 0, 99, "jersey number")
        if n is not None and p.team_id is not None and any(
                q.jersey_number == n and q.id != p.id for q in league.roster(p.team_id)):
            raise EditError(f"a teammate already wears #{n}")
        new["jersey_number"] = n
    attrs = _block(p.attributes, Attributes, changes.get("attributes") or {}, "ratings")
    hidden = _block(p.hidden, HiddenAttributes, changes.get("hidden") or {}, "hidden ratings")
    tend = _block(p.tendencies, Tendencies, changes.get("tendencies") or {}, "tendencies")
    pa = p.potential_ability
    if "potential_ability" in changes:
        pa = _int(changes["potential_ability"], ABILITY_MIN, ABILITY_MAX, "potential ability")
    morale = None
    if "morale" in changes:
        morale = _int(changes["morale"], 0, 100, "morale")
    contract = p.contract
    if "contract" in changes and changes["contract"] is not None:
        if p.team_id is None:
            raise EditError("a free agent has no contract to edit; sign him first")
        c = changes["contract"]
        cur = p.contract or Contract(league.rules.min_salary, league.season)
        hi = int(league.rules.salary_cap * league.rules.max_salary_cap_fraction)
        salary = _int(c.get("salary", cur.salary), 0, hi, "salary")
        expires = _int(c.get("expires_season", cur.expires_season), league.season, league.season + 6,
                       "contract end")
        opt = c.get("option", cur.option.value if cur.option else None)
        try:
            option = ContractOption(opt) if opt else None
        except ValueError:
            raise EditError("option must be player, team or none") from None
        contract = Contract(salary, expires, option, cur.rookie_scale)
    look = clean_look(changes["look"]) if "look" in changes else p.look
    team_id = p.team_id
    if "team_id" in changes:
        team_id = changes["team_id"]
        if team_id is not None:
            team_id = int(team_id)
            if team_id not in league.teams:
                raise EditError("no such team")
            if team_id != p.team_id and len(league.roster(team_id)) >= league.rules.max_roster:
                raise EditError(f"{league.teams[team_id].abbreviation} already has "
                                f"{league.rules.max_roster} players; release or move one first")
        if p.team_id is not None and team_id != p.team_id and len(league.roster(p.team_id)) <= league.rules.min_roster:
            raise EditError(f"{league.teams[p.team_id].abbreviation} needs at least {league.rules.min_roster} "
                            "players; add one first")

    # All checks passed: apply.
    before_ca = p.current_ability
    for k, v in new.items():
        if getattr(p, k) != v:
            setattr(p, k, v)
            done.append(k.replace("_", " "))
    for k, v in phys.items():
        if getattr(p.physique, k) != v:
            setattr(p.physique, k, v)
            done.append(k.split("_")[0])
    for label, attr, val in (("ratings", "attributes", attrs), ("hidden ratings", "hidden", hidden),
                             ("tendencies", "tendencies", tend)):
        if getattr(p, attr) != val:
            setattr(p, attr, val)
            done.append(label)
    old_pa = p.potential_ability
    p.potential_ability = max(pa, p.current_ability)
    if p.potential_ability != old_pa:
        done.append("potential")
    if morale is not None and morale != round(p.morale):
        p.morale = float(morale)
        done.append("morale")
    if contract is not p.contract:
        p.contract = contract
        done.append("contract")
    if (look or None) != (p.look or None):
        p.look = look
        done.append("look")
    if changes.get("heal") and p.injury:
        p.injury = None
        done.append("healed")
    old_team = p.team_id
    if team_id != old_team:
        p.team_id = team_id
        if team_id is None:
            p.contract = None
        elif p.contract is None:
            p.contract = Contract(league.rules.min_salary, league.season)
        if p.jersey_number is not None and team_id is not None and any(
                q.jersey_number == p.jersey_number and q.id != p.id for q in league.roster(team_id)):
            taken = {q.jersey_number for q in league.roster(team_id)}
            p.jersey_number = next(n for n in range(100) if n not in taken)
        done.append("team")
    for tid in {old_team, p.team_id} - {None}:
        _refresh_lineup(league, tid)
    if p.current_ability != before_ca and p.team_id is not None and "team" not in done:
        _refresh_lineup(league, p.team_id)
    if done:
        _note(league, f"{p.name}: {', '.join(dict.fromkeys(done))}")
    return list(dict.fromkeys(done))


def _refresh_lineup(league: League, team_id: int) -> None:
    """A changed roster: the coach re-picks the lineup, or for a hand-set lineup the
    departed players drop out of it."""
    team = league.teams[team_id]
    ids = {p.id for p in league.roster(team_id)}
    if team.auto_lineup or team_id != league.user_team_id:
        auto_lineup(league, team_id)
        return
    t = team.tactics
    if not set(t.starters) <= ids or not set(t.minutes_targets) <= ids:
        auto_lineup(league, team_id)


def edit_team(league: League, team_id: int, changes: dict) -> list[str]:
    team = league.teams.get(int(team_id))
    if team is None:
        raise EditError("no such team")
    allowed = {"city", "name", "abbreviation", "reputation", "budget", "arena_capacity"}
    unknown = set(changes) - allowed
    if unknown:
        raise EditError(f"can't edit {', '.join(sorted(unknown))}")
    new: dict[str, Any] = {}
    for key in ("city", "name"):
        if key in changes:
            v = str(changes[key]).strip()
            if not v or len(v) > 30:
                raise EditError(f"{key} must be 1-30 characters")
            new[key] = v
    if "abbreviation" in changes:
        v = str(changes["abbreviation"]).strip().upper()
        if not (2 <= len(v) <= 4 and v.isalnum()):
            raise EditError("abbreviation must be 2-4 letters or digits")
        if any(t.abbreviation == v and t.id != team.id for t in league.teams.values()):
            raise EditError(f"another team is already {v}")
        new["abbreviation"] = v
    if "reputation" in changes:
        new["reputation"] = _int(changes["reputation"], 1, 100, "reputation")
    if "budget" in changes:
        new["budget"] = _int(changes["budget"], 0, 10_000_000_000, "budget")
    if "arena_capacity" in changes:
        new["arena_capacity"] = _int(changes["arena_capacity"], 1000, 100_000, "arena capacity")
    done = [k.replace("_", " ") for k, v in new.items() if getattr(team, k) != v]
    for k, v in new.items():
        setattr(team, k, v)
    if done:
        _note(league, f"{team.full_name}: {', '.join(done)}")
    return done


def edited(league: League) -> Optional[dict]:
    log = league.info.get("edited") or []
    return {"count": len(log), "last": log[-1]} if log else None
