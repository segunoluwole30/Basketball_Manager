"""All-Star Weekend: fan voting, selections, Rising Stars, the Three-Point Contest
and the All-Star Game, on the calendar's All-Star break.

Timeline (days before the All-Star Game, a Sunday in mid-February):

* 42, 35, 28: fan voting returns (the leading vote-getters by conference and position).
* 24: starters. Per conference, two guards and three frontcourt players, ranked as the
  NBA does: half on the fan vote, half on the players' and media vote.
* 17: reserves, picked by the coaches: two guards, three frontcourt players, two
  wild cards.
* 14: the Rising Stars teams and the Three-Point Contest field.
* 3: the commissioner replaces injured All-Stars (an injured starter's place in the
  starting five goes to a reserve).
* 2 (Friday): Rising Stars: rookies against sophomores (or two drafted teams of
  young players when a league is too new to have both).
* 1 (Saturday): the Three-Point Contest. Five racks of five (four 1-point balls and
  a 2-point money ball each, plus one rack of money balls placed where the shooter
  likes), and two 3-point "deep" shots from 6 feet behind the line: 40 points at
  most. Eight shooters, the top three go to the final; ties are settled by a
  shoot-off.
* 0 (Sunday): the All-Star Game, East against West, with its MVP.

The exhibitions are played on a throwaway copy of the players (no injuries, no
stats): All-Star defence is famously relaxed and the pace high, so scores run high.
On the spatial or hybrid engine the games are recorded and can be watched.

State lives in ``league.all_star`` (this season); ``finish_season`` files a summary
in the season's history and marks career lines (``all_star``, ``asg_mvp``...).
"""
from __future__ import annotations

import copy
import json
import math
import random
import zlib
from datetime import timedelta
from typing import Optional

from .models import Game, League, Position, Team

GUARDS = {Position.PG, Position.SG}
# Exhibition replays live in league.replays under these fixed ids (overwritten each
# season, never pruned with ordinary game replays).
EVENT_REPLAY_BASE = 10_000_000
REPLAY_IDS = {"game": EVENT_REPLAY_BASE + 1, "rising": EVENT_REPLAY_BASE + 2}
EXHIBITION_TACTICS = (20, 17)        # pace and three-point rate: run and gun
PER_CONF = {"starters_g": 2, "starters_f": 3, "reserves_g": 2, "reserves_f": 3, "wild": 2}

EVENTS = [  # (key, days before the game)
    ("returns1", 42), ("returns2", 35), ("returns3", 28), ("starters", 24), ("reserves", 17),
    ("weekend_fields", 14), ("replacements", 3), ("rising", 2), ("three", 1), ("game", 0),
]


# --------------------------------------------------------------------------
# When
# --------------------------------------------------------------------------
def game_day(league: League) -> Optional[int]:
    """The All-Star Game's day number this season, or None (no break in a short season)."""
    from .schedule import season_calendar
    days = sorted({g.day for g in league.games.values() if g.season == league.season and not g.playoff})
    if not days:
        return None
    cal = season_calendar(league.season, league.rules.games_per_season, last_day=days[-1])
    d = cal.get("all_star_game")
    if d is None or not days[0] + 42 <= d < days[-1]:
        return None
    return d


def event_days(league: League) -> dict[str, int]:
    d = game_day(league)
    return {} if d is None else {key: d - before for key, before in EVENTS}


def _state(league: League) -> Optional[dict]:
    d = game_day(league)
    if d is None:
        return None
    st = league.all_star
    if not st or st.get("season") != league.season:
        st = league.all_star = {"season": league.season, "day": d, "done": []}
    return st


def catch_up(league: League, upto_day: int) -> list[str]:
    """Run every All-Star event dated on or before ``upto_day`` that hasn't happened.
    Returns headlines. Uses its own random stream, so the season's games don't change."""
    st = _state(league)
    if st is None:
        return []
    from .season import season_start
    out = []
    today = league.current_date
    for key, before in EVENTS:
        if key in st["done"] or st["day"] - before > upto_day:
            continue
        league.current_date = season_start(league.season) + timedelta(days=st["day"] - before)
        rng = random.Random(f"{league.name}|{league.season}|{key}")
        line = _RUN[key](league, st, rng)
        st["done"].append(key)
        if line:
            out.append(line)
    league.current_date = max(today, league.current_date) if out or st["done"] else today
    return out


# --------------------------------------------------------------------------
# Who: votes and selections
# --------------------------------------------------------------------------
def _season(league: League):
    from .playoffs import team_records
    from .stats import season_totals
    totals = season_totals(league)
    rec = team_records(league)
    pct = {tid: (w / (w + l) if w + l else 0.5) for tid, (w, l, _) in rec.items()}
    played = {tid: w + l for tid, (w, l, _) in rec.items()}
    return totals, pct, played


def _eligible(league: League, totals, played, share: float = 0.4) -> list[int]:
    out = []
    for pid, t in totals.items():
        p = league.players.get(pid)
        if p is None or p.team_id is None or p.retired:
            continue
        if t["gp"] >= max(1, share * played.get(p.team_id, 0)):
            out.append(pid)
    return out


def _past_selections(p) -> int:
    return sum(1 for c in p.career if c.get("all_star"))


def fan_votes(league: League) -> dict[int, int]:
    """Fan votes so far: fans love scorers, winners, big markets and familiar names."""
    from .stats import per_game
    totals, pct, played = _season(league)
    rng = random.Random(f"{league.name}|{league.season}|fans")
    votes = {}
    for pid in _eligible(league, totals, played, 0.3):
        t, p = totals[pid], league.players[pid]
        team = league.teams[p.team_id]
        appeal = (per_game(t, "pts") + 0.35 * per_game(t, "trb") + 0.5 * per_game(t, "ast")
                  + 0.12 * (team.reputation - 50) + 6 * pct.get(p.team_id, 0.5)
                  + 1.2 * min(5, _past_selections(p)) + 0.25 * (p.current_ability - 100))   # star power
        appeal = max(0.5, appeal) * math.exp(rng.gauss(0, 0.12))
        votes[pid] = int(round(appeal ** 2.6 * 220, -2))
    return votes


def _peer_score(league: League, t: dict, pid: int, pct) -> float:
    """What players, media and coaches see: production, winning, and how good he is."""
    from .stats import game_score
    p = league.players[pid]
    return game_score(t) + 5 * pct.get(p.team_id, 0.5) + 0.04 * p.current_ability


def _conf(league: League, pid: int) -> str:
    return league.teams[league.players[pid].team_id].conference


def _is_guard(league: League, pid: int) -> bool:
    return league.players[pid].position in GUARDS


def _returns(league, st, rng, n=None) -> str:
    votes = fan_votes(league)
    st["votes"] = votes
    st.setdefault("returns", []).append(league.current_date.isoformat())
    lines = []
    for conf in ("East", "West"):
        for label, guard in (("guards", True), ("frontcourt", False)):
            top = sorted((pid for pid in votes if _conf(league, pid) == conf and _is_guard(league, pid) == guard),
                         key=lambda pid: -votes[pid])[:3]
            lines.append(f"{conf} {label}: " + ", ".join(f"{league.players[pid].name} {votes[pid]:,}" for pid in top))
    mine = _user_lines(league, sorted(votes, key=lambda pid: -votes[pid]),
                       lambda pid, rank: f"{league.players[pid].name}: {votes[pid]:,} votes")
    _news(league, "allstar", f"All-Star voting: fan returns ({len(st['returns'])} of 3)", lines + mine)
    return f"All-Star fan voting returns: {lines[0]}"


def _starters(league, st, rng) -> str:
    totals, pct, played = _season(league)
    votes = st.get("votes") or fan_votes(league)
    pool = _eligible(league, totals, played)
    peer = {pid: _peer_score(league, totals[pid], pid, pct) * math.exp(rng.gauss(0, 0.04)) for pid in pool}
    for conf in ("East", "West"):
        side = st.setdefault(conf.lower(), {"starters": [], "reserves": [], "replaced": []})
        side["starters"] = []
        for guard, n in ((True, PER_CONF["starters_g"]), (False, PER_CONF["starters_f"])):
            group = [pid for pid in pool if _conf(league, pid) == conf and _is_guard(league, pid) == guard]
            fan_rank = {pid: r for r, pid in enumerate(sorted(group, key=lambda x: -votes.get(x, 0)))}
            peer_rank = {pid: r for r, pid in enumerate(sorted(group, key=lambda x: -peer[x]))}
            ranked = sorted(group, key=lambda x: (0.5 * fan_rank[x] + 0.5 * peer_rank[x], fan_rank[x]))
            side["starters"] += ranked[:n]
    _honour(league, st["east"]["starters"] + st["west"]["starters"])
    body = [f"{c}: " + ", ".join(_label(league, pid) for pid in st[c.lower()]["starters"]) for c in ("East", "West")]
    body += _user_lines(league, st["east"]["starters"] + st["west"]["starters"],
                        lambda pid, _: f"{league.players[pid].name} will start the All-Star Game!")
    _news(league, "allstar", "All-Star starters announced", body)
    return "All-Star starters: " + "; ".join(body[:2])


def _reserves(league, st, rng) -> str:
    totals, pct, played = _season(league)
    pool = _eligible(league, totals, played)
    peer = {pid: _peer_score(league, totals[pid], pid, pct) * math.exp(rng.gauss(0, 0.05)) for pid in pool}
    for conf in ("East", "West"):
        side = st[conf.lower()]
        taken = set(side["starters"])
        avail = sorted((pid for pid in pool if _conf(league, pid) == conf and pid not in taken), key=lambda x: -peer[x])
        res = [pid for pid in avail if _is_guard(league, pid)][:PER_CONF["reserves_g"]]
        res += [pid for pid in avail if not _is_guard(league, pid)][:PER_CONF["reserves_f"]]
        res += [pid for pid in avail if pid not in res][:PER_CONF["wild"]]
        side["reserves"] = res
    _honour(league, st["east"]["reserves"] + st["west"]["reserves"])
    body = [f"{c}: " + ", ".join(_label(league, pid) for pid in st[c.lower()]["reserves"]) for c in ("East", "West")]
    me = league.user_team_id
    if me is not None:
        mine = [pid for c in ("east", "west") for pid in st[c]["starters"] + st[c]["reserves"]
                if league.players[pid].team_id == me]
        body.append("Your All-Stars: " + (", ".join(league.players[pid].name for pid in mine) if mine else "none this year."))
        snubs = sorted((pid for pid in pool if league.players[pid].team_id == me and pid not in mine),
                       key=lambda x: -peer[x])[:1]
        if snubs and peer[snubs[0]] >= min(peer.get(pid, 0) for c in ("east", "west") for pid in st[c]["reserves"]) - 2:
            body.append(f"{league.players[snubs[0]].name} was among the last left out: a snub, say the fans.")
    _news(league, "allstar", "All-Star reserves announced", body)
    return "All-Star reserves: " + "; ".join(body[:2])


def _weekend_fields(league, st, rng) -> str:
    totals, pct, played = _season(league)
    st["rising"] = _rising_teams(league, totals, pct, played)
    ranked = _three_field(league, totals, played, n=14)
    st["three"] = {"field": ranked[:8], "alternates": ranked[8:]}
    body = [f"{t['name']}: " + ", ".join(_label(league, pid) for pid in t["players"]) for t in st["rising"]["teams"]]
    body.append("Three-Point Contest: " + ", ".join(_label(league, pid) for pid in st["three"]["field"]))
    body += _user_lines(league, st["three"]["field"], lambda pid, _: f"{league.players[pid].name} is in the Three-Point Contest.")
    _news(league, "allstar", "Rising Stars and Three-Point Contest fields", body)
    return body[-1] if not league.user_team_id else "All-Star Weekend fields set"


def _rising_teams(league, totals, pct, played) -> dict:
    s = league.season
    score = {pid: _peer_score(league, t, pid, pct) for pid, t in totals.items()
             if pid in league.players and league.players[pid].team_id is not None}
    def first_years(year):
        return sorted((pid for pid in score if league.players[pid].draft_year == year and totals[pid]["gp"] >= 3),
                      key=lambda x: -score[x])
    rookies, sophs = first_years(s), first_years(s - 1)
    if len(rookies) >= 7 and len(sophs) >= 7:
        return {"format": "rookies_sophomores",
                "teams": [{"name": "Team Rookies", "players": rookies[:9]}, {"name": "Team Sophomores", "players": sophs[:9]}]}
    young = [pid for pid in score if league.players[pid].draft_year in (s, s - 1)
             or league.players[pid].age(league.current_date) <= 22]
    young = sorted((pid for pid in young if totals[pid]["gp"] >= 3), key=lambda x: -score[x])[:18]
    a, b = [], []
    for i, pid in enumerate(young):                 # snake draft
        (a if i % 4 in (0, 3) else b).append(pid)
    return {"format": "draft", "teams": [{"name": "Team Rise", "players": a}, {"name": "Team Shine", "players": b}]}


def _three_field(league, totals, played, n: int = 8) -> list[int]:
    def shooter(pid):
        t, p = totals[pid], league.players[pid]
        pct = (t["tpm"] + 0.35 * 100) / (t["tpa"] + 100)            # shrunk toward league-ish average
        return 100 * pct + 2.5 * t["tpm"] / max(1, t["gp"]) + 0.6 * p.attributes.three_point
    pool = [pid for pid in _eligible(league, totals, played, 0.5)
            if totals[pid]["tpa"] / max(1, totals[pid]["gp"]) >= 3]
    if len(pool) < n:
        pool = _eligible(league, totals, played, 0.2)
    return sorted(pool, key=lambda x: -shooter(x))[:n]


def _replacements(league, st, rng) -> str:
    """The commissioner names healthy replacements for injured All-Stars; an injured
    starter's place in the starting five goes to the best healthy reserve."""
    totals, pct, played = _season(league)
    pool = _eligible(league, totals, played)
    peer = {x: _peer_score(league, totals[x], x, pct) for x in pool}
    picked = {pid for c in ("east", "west") for pid in st[c]["starters"] + st[c]["reserves"]}
    hurt = lambda pid: bool(league.players[pid].injury)  # noqa: E731
    lines = []
    for conf in ("East", "West"):
        side = st[conf.lower()]
        out = [pid for pid in side["starters"] + side["reserves"] if hurt(pid)]
        for pid in out:
            cands = sorted((x for x in pool if _conf(league, x) == conf and x not in picked and not hurt(x)),
                           key=lambda x: -peer[x])
            if not cands:
                continue
            new = cands[0]
            picked.add(new)
            side["replaced"].append({"out": pid, "in": new})
            if pid in side["starters"]:
                healthy = sorted((x for x in side["reserves"] if not hurt(x)), key=lambda x: -peer.get(x, 0))
                promo = healthy[0] if healthy else new
                side["starters"][side["starters"].index(pid)] = promo
                if promo != new:
                    side["reserves"][side["reserves"].index(promo)] = new
            else:
                side["reserves"][side["reserves"].index(pid)] = new
            _honour(league, [new])
            lines.append(f"{league.players[new].name} replaces the injured {league.players[pid].name} ({conf}).")
    three = st.get("three")
    if three:                                        # next shooters up take an injured shooter's place
        alts = [x for x in three.get("alternates", []) if not hurt(x)]
        three["field"] = [x if not hurt(x) else (alts.pop(0) if alts else None) for x in three["field"]]
        three["field"] = [x for x in three["field"] if x is not None]
    if st.get("rising"):
        for t in st["rising"]["teams"]:
            t["players"] = [pid for pid in t["players"] if not hurt(pid)]
    if lines:
        _news(league, "allstar", "All-Star injury replacements", lines)
    return lines[0] if lines else ""


def _honour(league: League, pids: list[int]) -> None:
    for pid in pids:                                   # being picked lifts a player's spirits
        p = league.players[pid]
        p.morale = min(100.0, p.morale + 6)


# --------------------------------------------------------------------------
# The exhibitions
# --------------------------------------------------------------------------
def _exhibition(league: League, rng: random.Random, home: tuple[str, str, list[int]],
                away: tuple[str, str, list[int]], relax: int, replay_key: str) -> dict:
    """Play an exhibition between two hand-picked teams on copies of the players."""
    from .engine import simulate_game
    from .lineup import auto_lineup
    rules = copy.deepcopy(league.rules)
    mini = League(name=league.name, season=league.season, current_date=league.current_date, rules=rules)
    start = {}
    for tid, (abbr, name, pids) in ((1, home), (2, away)):
        city, _, nick = name.partition(" ")
        mini.add_team(Team(id=tid, city=city, name=nick, abbreviation=abbr, conference="East",
                           division="Atlantic", reputation=100))
        team = mini.teams[tid]
        team.tactics.pace, team.tactics.three_point_rate = EXHIBITION_TACTICS
        team.tactics.rotation_depth = max(5, min(15, len(pids)))
        used = set()
        for pid in pids:
            p = copy.deepcopy(league.players[pid])
            p.team_id, p.injury, p.morale = tid, None, 75.0
            if p.jersey_number in used:
                p.jersey_number = next(n for n in range(100) if n not in used)
            used.add(p.jersey_number)
            a = p.attributes
            for k in ("perimeter_defense", "interior_defense", "stealing", "blocking"):
                setattr(a, k, max(1, getattr(a, k) - relax))   # nobody wants to get hurt in February
            mini.add_player(p)
        auto_lineup(mini, tid)
        start[tid] = pids
    game = Game(id=1, season=league.season, day=league.all_star.get("day", 0) if league.all_star else 0,
                home_team_id=1, away_team_id=2)
    mini.add_game(game)
    spatial = rules.user_games_spatial
    if spatial:
        from .spatial.season_game import simulate_spatial
        sim = simulate_spatial(mini, game, rng, record=True)
    else:
        rules.match_engine = "statistical"
        sim = simulate_game(mini, game, rng)
    if sim.replay is not None:
        league.replays[REPLAY_IDS[replay_key]] = sim.replay
    def lines(box):
        out = []
        for ln in box.lines:
            if ln.seconds <= 0:
                continue
            out.append({"id": ln.player_id, "name": league.players[ln.player_id].name,
                        "team": league.teams[league.players[ln.player_id].team_id].abbreviation
                        if league.players[ln.player_id].team_id else "",
                        "min": round(ln.seconds / 60), "pts": ln.pts, "reb": ln.orb + ln.drb, "ast": ln.ast,
                        "stl": ln.stl, "blk": ln.blk, "fg": f"{ln.fgm}-{ln.fga}", "tp": f"{ln.tpm}-{ln.tpa}",
                        "gs": ln.pts + 0.4 * ln.fgm - 0.7 * ln.fga + 0.7 * ln.orb + 0.3 * ln.drb
                              + ln.stl + 0.7 * ln.ast + 0.7 * ln.blk - ln.tov})
        return sorted(out, key=lambda r: -r["pts"])
    h, a = sim.box_score.home, sim.box_score.away
    winner_box = h if sim.result.home_score > sim.result.away_score else a
    mvp = max(lines(winner_box), key=lambda r: r["gs"])["id"]
    return {"home": home[1], "away": away[1], "home_abbr": home[0], "away_abbr": away[0],
            "score": [sim.result.home_score, sim.result.away_score], "ot": sim.result.overtimes,
            "box": {"home": lines(h), "away": lines(a)}, "mvp": mvp,
            "replay_id": REPLAY_IDS[replay_key] if sim.replay is not None else None}


def _rising(league, st, rng) -> str:
    r = st.get("rising")
    if not r or any(len(t["players"]) < 5 for t in r["teams"]):
        return ""
    t1, t2 = r["teams"]
    abbr = lambda n: n.replace("Team ", "")[:4].upper()  # noqa: E731
    r["result"] = _exhibition(league, rng, (abbr(t1["name"]), t1["name"], t1["players"]),
                              (abbr(t2["name"]), t2["name"], t2["players"]), relax=5, replay_key="rising")
    res = r["result"]
    win = res["home"] if res["score"][0] > res["score"][1] else res["away"]
    line = (f"Rising Stars: {res['home']} {res['score'][0]}, {res['away']} {res['score'][1]}. "
            f"MVP {league.players[res['mvp']].name}.")
    _news(league, "allstar", f"Rising Stars: {win} win", [line], **_watch(res))
    return line


def _game(league, st, rng) -> str:
    if "east" not in st:
        return ""
    def roster(conf):
        side = st[conf]
        return [pid for pid in side["starters"] + side["reserves"] if not league.players[pid].injury] or side["starters"]
    east, west = roster("east"), roster("west")
    # The starters start: put them first so the coach's lineup picks them.
    res = _exhibition(league, rng, ("WEST", "West All-Stars", west), ("EAST", "East All-Stars", east),
                      relax=10, replay_key="game")
    st["game"] = res
    winner = res["home"] if res["score"][0] > res["score"][1] else res["away"]
    mvp = league.players[res["mvp"]]
    mvp_line = next(r for side in ("home", "away") for r in res["box"][side] if r["id"] == mvp.id)
    line = (f"All-Star Game: {res['away']} {res['score'][1]}, {res['home']} {res['score'][0]}"
            f"{' (OT)' if res['ot'] else ''}. MVP {mvp.name} ({mvp_line['pts']} pts, "
            f"{mvp_line['reb']} reb, {mvp_line['ast']} ast).")
    body = [line]
    me = league.user_team_id
    if me is not None:
        for side in ("home", "away"):
            for r in res["box"][side]:
                if league.players[r["id"]].team_id == me:
                    body.append(f"{r['name']}: {r['pts']} pts, {r['reb']} reb, {r['ast']} ast in {r['min']} min.")
    _news(league, "allstar", f"All-Star Game: the {winner} win", body, **_watch(res))
    return line


# --------------------------------------------------------------------------
# Three-Point Contest
# --------------------------------------------------------------------------
def shoot_round(p, rng: random.Random, final: bool = False) -> dict:
    """One round: five racks (one all money balls) and two deep shots. Each ball is
    the points it scored (0 = miss); a rack's last ball is the money ball."""
    a = p.attributes
    base = 0.24 + 0.022 * a.three_point + (0.005 * (a.composure - 10) if final else 0)
    money_rack = rng.randrange(5)
    racks, deep = [], []
    shots = 0
    for r in range(5):
        rack = []
        for b in range(5):
            tired = 0.004 * shots                         # the 70 seconds take their toll
            value = 2 if (r == money_rack or b == 4) else 1
            rack.append(value if rng.random() < max(0.05, base - tired) else 0)
            shots += 1
        racks.append(rack)
        if r in (1, 2):                                   # deep balls between racks 2-3 and 3-4
            deep.append(3 if rng.random() < max(0.03, base - 0.15 - 0.004 * shots) else 0)
            shots += 1
    return {"racks": racks, "deep": deep, "money_rack": money_rack,
            "points": sum(map(sum, racks)) + sum(deep)}


def _shootoff(league, ids, rng, final):
    """Tied shooters each shoot one more rack until one is ahead; returns them ordered."""
    rounds = []
    while True:
        res = {}
        for pid in ids:
            p = league.players[pid]
            base = 0.24 + 0.022 * p.attributes.three_point
            res[pid] = sum((2 if b == 4 else 1) for b in range(5) if rng.random() < base)
        rounds.append(res)
        best = max(res.values())
        if sum(1 for v in res.values() if v == best) == 1 or len(rounds) >= 6:
            return sorted(ids, key=lambda x: -res[x]), rounds


def _three(league, st, rng) -> str:
    c = st.get("three")
    if not c or len(c["field"]) < 4:
        return ""
    first = {pid: shoot_round(league.players[pid], rng) for pid in c["field"]}
    order = sorted(first, key=lambda x: -first[x]["points"])
    finalists = order[:3]
    cut = first[order[2]]["points"]
    tied = [pid for pid in order if first[pid]["points"] == cut]
    shootoffs = []
    if len(tied) > 1 and len(order) > 3 and first[order[3]]["points"] == cut:
        above = [pid for pid in order if first[pid]["points"] > cut]
        ranked, rounds = _shootoff(league, tied, rng, False)
        finalists = above + ranked[:3 - len(above)]
        shootoffs.append({"stage": "first round", "players": tied, "rounds": rounds})
    final = {pid: shoot_round(league.players[pid], rng, final=True) for pid in finalists}
    forder = sorted(final, key=lambda x: -final[x]["points"])
    top = final[forder[0]]["points"]
    tied = [pid for pid in forder if final[pid]["points"] == top]
    if len(tied) > 1:
        ranked, rounds = _shootoff(league, tied, rng, True)
        forder = ranked + [pid for pid in forder if pid not in ranked]
        shootoffs.append({"stage": "final", "players": tied, "rounds": rounds})
    c.update({"first": {str(k): v for k, v in first.items()}, "final": {str(k): v for k, v in final.items()},
              "finalists": finalists, "winner": forder[0], "shootoffs": shootoffs})
    w = league.players[forder[0]]
    line = (f"Three-Point Contest: {w.name} wins with {final[w.id]['points']} in the final "
            f"(" + ", ".join(f"{league.players[pid].name} {final[pid]['points']}" for pid in forder[1:]) + ").")
    body = [line, "First round: " + ", ".join(f"{league.players[pid].name} {first[pid]['points']}" for pid in order)]
    _news(league, "allstar", f"{w.name} wins the Three-Point Contest", body)
    return line


_RUN = {"returns1": _returns, "returns2": _returns, "returns3": _returns, "starters": _starters,
        "reserves": _reserves, "weekend_fields": _weekend_fields, "replacements": _replacements,
        "rising": _rising, "three": _three, "game": _game}


# --------------------------------------------------------------------------
# News, history
# --------------------------------------------------------------------------
def _label(league: League, pid: int) -> str:
    p = league.players[pid]
    team = league.teams[p.team_id].abbreviation if p.team_id else "FA"
    return f"{p.name} ({team})"


def _user_lines(league, pids, fmt) -> list[str]:
    me = league.user_team_id
    if me is None:
        return []
    return [fmt(pid, i) for i, pid in enumerate(pids) if league.players[pid].team_id == me][:3]


def _watch(res: dict) -> dict:
    return {"replay_id": res["replay_id"]} if res.get("replay_id") else {}


def _news(league: League, kind: str, title: str, body: list[str], **extra) -> None:
    if league.user_team_id is None:
        return
    from .news import post
    post(league, kind, title, body, **extra)


def selected(league: League) -> set[int]:
    st = league.all_star
    if not st or st.get("season") != league.season or "east" not in st:
        return set()
    return {pid for c in ("east", "west") if c in st for pid in st[c]["starters"] + st[c]["reserves"]}


def season_summary(league: League) -> Optional[dict]:
    """A compact record for the season's history (None if there was no All-Star Game)."""
    st = league.all_star
    if not st or st.get("season") != league.season or "game" not in st:
        return None
    g = st["game"]
    out = {"east": {"starters": st["east"]["starters"], "reserves": st["east"]["reserves"]},
           "west": {"starters": st["west"]["starters"], "reserves": st["west"]["reserves"]},
           "score": {g["away"]: g["score"][1], g["home"]: g["score"][0]}, "mvp": g["mvp"],
           "mvp_name": league.players[g["mvp"]].name}
    if st.get("three", {}).get("winner") is not None:
        w = st["three"]["winner"]
        out["three_point_champion"] = w
        out["three_point_champion_name"] = league.players[w].name
    r = st.get("rising", {}).get("result")
    if r:
        out["rising_stars_mvp"] = r["mvp"]
        out["rising_stars_mvp_name"] = league.players[r["mvp"]].name
    return out


def career_flags(league: League) -> dict[int, dict]:
    """{player id: {"all_star": True, "asg_mvp": True, ...}} for this season's career lines."""
    st = league.all_star
    flags: dict[int, dict] = {}
    if not st or st.get("season") != league.season:
        return flags
    for pid in selected(league):
        flags.setdefault(pid, {})["all_star"] = True
    if st.get("game"):
        flags.setdefault(st["game"]["mvp"], {})["asg_mvp"] = True
    if st.get("three", {}).get("winner") is not None:
        flags.setdefault(st["three"]["winner"], {})["three_point_champion"] = True
    if st.get("rising", {}).get("result"):
        flags.setdefault(st["rising"]["result"]["mvp"], {})["rising_stars_mvp"] = True
    return flags


def honours(p) -> dict:
    """A player's All-Star honours over his career (finished seasons)."""
    return {k: sum(1 for c in p.career if c.get(k) and not c.get("playoffs"))
            for k in ("all_star", "asg_mvp", "three_point_champion", "rising_stars_mvp")}


def replay(league: League, replay_id: int) -> Optional[dict]:
    blob = league.replays.get(int(replay_id))
    return json.loads(zlib.decompress(blob)) if blob else None


# --------------------------------------------------------------------------
# For the web app
# --------------------------------------------------------------------------
EVENT_LABELS = {"returns1": "Fan voting returns", "returns2": "Fan voting returns", "returns3": "Final fan returns",
                "starters": "All-Star starters announced", "reserves": "All-Star reserves announced",
                "weekend_fields": "Rising Stars and Three-Point fields", "replacements": "Injury replacements",
                "rising": "Rising Stars", "three": "Three-Point Contest", "game": "All-Star Game"}


def view(league: League) -> dict:
    """Everything the All-Star screen shows, as far as the season has got."""
    from .season import season_start
    from .stats import per_game, season_totals
    days = event_days(league)
    if not days:
        return {"available": False, "past": _past(league)}
    st = league.all_star if league.all_star and league.all_star.get("season") == league.season else {"done": []}
    base = season_start(league.season)
    totals = season_totals(league)
    me = league.user_team_id

    def row(pid):
        p = league.players[pid]
        t = totals.get(pid)
        return {"id": pid, "name": p.name, "pos": p.position.value,
                "team": league.teams[p.team_id].abbreviation if p.team_id else "FA",
                "mine": p.team_id is not None and p.team_id == me, "injured": bool(p.injury),
                "ppg": round(per_game(t, "pts"), 1) if t else 0.0, "rpg": round(per_game(t, "trb"), 1) if t else 0.0,
                "apg": round(per_game(t, "ast"), 1) if t else 0.0,
                "tpm": int(t["tpm"]) if t else 0, "tpa": int(t["tpa"]) if t else 0}

    out = {"available": True, "season": league.season,
           "schedule": [{"key": k, "label": EVENT_LABELS[k], "date": (base + timedelta(days=d)).isoformat(),
                         "done": k in st["done"]} for k, d in sorted(days.items(), key=lambda kv: kv[1])
                        if k not in ("returns2",)],
           "game_date": (base + timedelta(days=days["game"])).isoformat()}
    votes = st.get("votes")
    if votes:
        out["votes"] = {}
        for conf in ("East", "West"):
            for label, guard in (("Guards", True), ("Frontcourt", False)):
                top = sorted((pid for pid in votes if pid in league.players and league.players[pid].team_id
                              and _conf(league, pid) == conf and _is_guard(league, pid) == guard),
                             key=lambda pid: -votes[pid])[:8]
                out["votes"][f"{conf} {label}"] = [{**row(pid), "votes": votes[pid]} for pid in top]
    for conf in ("east", "west"):
        if conf in st:
            side = st[conf]
            out[conf] = {"starters": [row(p) for p in side["starters"]], "reserves": [row(p) for p in side["reserves"]],
                         "replaced": [{"out": league.players[r["out"]].name, "in": league.players[r["in"]].name}
                                      for r in side["replaced"]]}
    if st.get("rising"):
        r = st["rising"]
        out["rising"] = {"format": r["format"], "teams": [{"name": t["name"], "players": [row(p) for p in t["players"]]}
                                                          for t in r["teams"]], "result": r.get("result")}
    if st.get("three"):
        c = st["three"]
        out["three"] = {"field": [row(p) for p in c["field"]], "winner": c.get("winner"),
                        "first": c.get("first"), "final": c.get("final"), "finalists": c.get("finalists"),
                        "shootoffs": c.get("shootoffs", [])}
    if st.get("game"):
        out["game"] = st["game"]
    out["past"] = _past(league)
    return out


def _past(league: League) -> list[dict]:
    out = []
    for h in reversed(league.history):
        a = h.get("all_star")
        if a:
            out.append({"season": h["season"], "score": a["score"], "mvp": a["mvp"], "mvp_name": a["mvp_name"],
                        "three_point_champion": a.get("three_point_champion"),
                        "three_point_champion_name": a.get("three_point_champion_name"),
                        "rising_stars_mvp": a.get("rising_stars_mvp"),
                        "rising_stars_mvp_name": a.get("rising_stars_mvp_name")})
    return out
