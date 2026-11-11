"""An NBA-shaped regular-season schedule.

**Calendar** (``season_calendar``), counted in days from ``season_start``:

* Opening night is the first Tuesday from 21 October. An 82-game season ends on
  a Sunday about 173 days later (mid-April); shorter seasons are compressed.
* There are no games on Thanksgiving (4th Thursday of November) or Christmas Eve.
* Christmas Day has a short marquee slate (at most 5 games).
* The All-Star break runs Friday to Wednesday around the third Sunday of February.

**Games** (``build_schedule``):

* Every team plays exactly ``games_per_season`` games, half at home.
* Opponents are weighted like the NBA: about 37% against the other conference,
  the rest against your own.

**Dates.** Games are placed day by day, greedily:

* Each team is scheduled by how far behind it is, with gaps mostly of one rest day.
* Back-to-backs are allowed (about 14-16 a team over 82 games) but never three
  games in three nights.
* Teams on the road tend to stay on the road (road trips), and the same two teams
  don't meet again within a few days.
* The league slate follows the NBA's weekday rhythm: Wednesday and Friday busy,
  Monday and Thursday lighter.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Optional

from .models import Game, League

WEEKDAY_LOAD = [0.85, 1.05, 1.15, 0.8, 1.15, 1.05, 0.95]   # Mon..Sun, share of an average night
NBA_SPAN = 173                 # opening night to the last day of an 82-game season
B2B_SHARE = 0.17               # NBA: ~15 back-to-backs in 82 games (about 18% of the gaps)
B2B_NUDGE = 0.45               # pull toward (behind the quota) or away from (ahead of it) a back-to-back
INTER_CONFERENCE_SHARE = 0.37  # NBA: 30 of 82 games against the other conference
CHRISTMAS_GAMES = 5
MAX_ROAD_TRIP = 6              # longest run of away games (NBA: rarely more than 6)
MAX_HOMESTAND = 7


def season_start(season: int) -> date:
    from .season import season_start as start
    return start(season)


def _first_weekday_on_or_after(d: date, weekday: int) -> date:
    return d + timedelta(days=(weekday - d.weekday()) % 7)


def season_calendar(season: int, games_per_season: int = 82, last_day: Optional[int] = None) -> dict:
    """Key dates of the regular season as day numbers (days since ``season_start``)."""
    base = season_start(season)
    opening = _first_weekday_on_or_after(date(season, 10, 21), 1)          # a Tuesday
    span = max(int(games_per_season * 2.0) + 2, round(NBA_SPAN * games_per_season / 82))
    end = opening + timedelta(days=span)
    if games_per_season >= 60:
        end = _first_weekday_on_or_after(end, 6)                            # it ends on a Sunday
    thanksgiving = _first_weekday_on_or_after(date(season, 11, 1), 3) + timedelta(days=21)
    christmas_eve, christmas = date(season, 12, 24), date(season, 12, 25)
    asg = _first_weekday_on_or_after(date(season + 1, 2, 1), 6) + timedelta(days=14)   # 3rd Sunday
    all_star = [asg + timedelta(days=k) for k in range(-2, 4)]                          # Fri..Wed
    day = lambda d: (d - base).days
    first, last = day(opening), day(end)
    if last_day is not None:
        last = last_day
    out = {"opening": first, "end": last, "blackout": [], "christmas": None, "all_star": [],
           "all_star_game": None}
    for d in (thanksgiving, christmas_eve):
        if first <= day(d) <= last:
            out["blackout"].append(day(d))
    if first <= day(christmas) <= last:
        out["christmas"] = day(christmas)
    if first <= day(asg) <= last:
        out["all_star"] = [day(d) for d in all_star]
        out["all_star_game"] = day(asg)
        out["blackout"] += out["all_star"]
    return out


# ------------------------------------------------------------------ who plays whom
def _regular_pairs(members: list[int], degree: int, rng: random.Random) -> list[tuple[int, int]]:
    """A ``degree``-regular set of pairs among ``members`` (a circulant graph)."""
    m = len(members)
    order = members[:]
    rng.shuffle(order)
    pairs = set()
    for i in range(m):
        for k in range(1, degree // 2 + 1):
            a, b = order[i], order[(i + k) % m]
            pairs.add((min(a, b), max(a, b)))
        if degree % 2:
            a, b = order[i], order[(i + m // 2) % m]
            pairs.add((min(a, b), max(a, b)))
    return sorted(pairs)


def _cross_division_pairs(members: list[int], div: dict[int, str], degree: int,
                          rng: random.Random) -> Optional[list[tuple[int, int]]]:
    """A ``degree``-regular set of pairs between teams of *different* divisions of one
    conference, or None if this construction can't make one."""
    if degree == 0:
        return []
    groups: dict[str, list[int]] = {}
    for t in members:
        groups.setdefault(div[t], []).append(t)
    d = len(groups)
    sizes = {len(g) for g in groups.values()}
    m = len(members)
    if d < 2 or len(sizes) != 1 or (m * degree) % 2:
        return None
    # Interleave the divisions (A1 B1 C1 A2 B2 C2 ...): an offset that isn't a multiple
    # of the division count always lands in another division.
    cols = [rng.sample(g, len(g)) for g in groups.values()]
    order = [cols[j][i] for i in range(len(cols[0])) for j in range(d)]
    offsets = [s for s in range(1, (m + 1) // 2) if s % d]
    rng.shuffle(offsets)
    use = offsets[:degree // 2]
    if degree % 2:
        if m % 2 or (m // 2) % d == 0:
            return _random_regular_cross(members, div, degree, rng)
        use.append(m // 2)
    if len(use) * 2 - (1 if degree % 2 else 0) == degree:
        pairs = set()
        for i in range(m):
            for s_ in use:
                a, b = order[i], order[(i + s_) % m]
                pairs.add((min(a, b), max(a, b)))
        return sorted(pairs)
    return _random_regular_cross(members, div, degree, rng)


def _random_regular_cross(members, div, degree, rng, tries: int = 400):
    """Small conferences the interleaving can't handle: random greedy attempts."""
    allowed = [(a, b) for i, a in enumerate(members) for b in members[i + 1:] if div[a] != div[b]]
    for _ in range(tries):
        rng.shuffle(allowed)
        deg = {t: 0 for t in members}
        pairs = []
        for a, b in allowed:
            if deg[a] < degree and deg[b] < degree:
                pairs.append((min(a, b), max(a, b)))
                deg[a] += 1
                deg[b] += 1
        if all(v == degree for v in deg.values()):
            return sorted(pairs)
    return None


def matchup_counts(league: League, games: int, rng: random.Random) -> dict[tuple[int, int], int]:
    """How many times each pair meets so that every team plays ``games``.

    Two equal conferences get the NBA's formula: a fixed number against each team in
    the other conference (2 in an 82-game, 30-team league), and the rest within the
    conference, where division rivals get the extra games first (4 each) and the
    remaining extras go to other conference teams (4 against six of them, 3 against
    the other four)."""
    teams = sorted(league.teams)
    n = len(teams)
    div = {t: league.teams[t].division for t in teams}
    confs: dict[str, list[int]] = {}
    for t in teams:
        confs.setdefault(league.teams[t].conference, []).append(t)
    groups = list(confs.values())
    if len(groups) == 2 and len(groups[0]) == len(groups[1]) and len(groups[0]) >= 2:
        counts = _nba_formula(groups, div, games, rng)
        if counts is not None:
            return counts
    # Any other league: as even as possible, the odd games against neighbours.
    counts: dict[tuple[int, int], int] = {}
    base, extra = divmod(games, n - 1)
    if (n * extra) % 2:
        extra -= 1                                       # an odd total can't be split evenly
    for i, a in enumerate(teams):
        for b in teams[i + 1:]:
            counts[(a, b)] = base
    for pair in _regular_pairs(teams, extra, rng):
        counts[pair] += 1
    return counts


def _nba_formula(groups, div, games, rng):
    size = len(groups[0])
    inter = max(1, round(INTER_CONFERENCE_SHARE * games / size))
    rest = games - inter * size                          # conference games per team
    if rest < 0 or size < 2:
        return None
    base, extra = divmod(rest, size - 1)
    counts: dict[tuple[int, int], int] = {}
    for a in groups[0]:
        for b in groups[1]:
            counts[(min(a, b), max(a, b))] = inter
    for g in groups:
        for i, a in enumerate(g):
            for b in g[i + 1:]:
                counts[(min(a, b), max(a, b))] = base
        rivals = {t: [x for x in g if x != t and div[x] == div[t]] for t in g}
        k = {len(v) for v in rivals.values()}
        if len(k) != 1:
            return None                                  # unequal divisions: use the even split
        k = k.pop()
        if extra >= k:
            for t in g:                                  # division rivals: one more game each
                for x in rivals[t]:
                    if t < x:
                        counts[(t, x)] += 1
            cross = _cross_division_pairs(g, div, extra - k, rng)
            if cross is None:
                return None
            for pair in cross:
                counts[pair] += 1
        else:
            for name in {div[t] for t in g}:
                members = [t for t in g if div[t] == name]
                if (len(members) * extra) % 2:
                    return None
                for pair in _regular_pairs(members, extra, rng):
                    counts[pair] += 1
    return counts


def _home_away(counts: dict[tuple[int, int], int], rng: random.Random) -> list[tuple[int, int]]:
    """(home, away) for every game, splitting each pair evenly and balancing the odd
    games so each team is as close to half at home as possible."""
    games, odd = [], []
    homes: dict[int, int] = {}
    for (a, b), k in counts.items():
        for _ in range(k // 2):
            games += [(a, b), (b, a)]
        homes[a] = homes.get(a, 0) + k // 2
        homes[b] = homes.get(b, 0) + k // 2
        if k % 2:
            odd.append([a, b])
    rng.shuffle(odd)
    for e in odd:                                       # greedy: the team with fewer home games hosts
        a, b = e
        if homes.get(a, 0) > homes.get(b, 0):
            e.reverse()
        homes[e[0]] = homes.get(e[0], 0) + 1
    # Repair: a team with too many home games hands one along a chain of odd games
    # (each flipped) to a team with too few, until everyone is as even as can be.
    target = {t: sum(k for (a, b), k in counts.items() if t in (a, b)) / 2 for t in homes}
    for _ in range(200):
        over = [t for t in homes if homes[t] > target[t] + 0.5]
        if not over:
            break
        start = over[0]
        prev: dict[int, tuple] = {start: None}
        queue, found = [start], None
        while queue and found is None:
            u = queue.pop(0)
            for e in odd:
                if e[0] == u and e[1] not in prev:
                    prev[e[1]] = (u, e)
                    if homes[e[1]] < target[e[1]] - 0.5:
                        found = e[1]
                        break
                    queue.append(e[1])
        if found is None:
            break
        v = found
        while prev[v] is not None:                      # flip the chain back to the start
            u, e = prev[v]
            e.reverse()
            v = u
        homes[start] -= 1
        homes[found] += 1
    return games + [tuple(e) for e in odd]


# ------------------------------------------------------------------ when
def _place(games: list[tuple[int, int]], teams: list[int], days: list[int], caps: dict[int, float],
           rng: random.Random) -> tuple[dict[int, list[tuple[int, int]]], list[tuple[int, int]]]:
    pool: dict[tuple[int, int], int] = {}
    for g in games:
        pool[g] = pool.get(g, 0) + 1
    left = {t: 0 for t in teams}
    for h, a in games:
        left[h] += 1
        left[a] += 1
    last = {t: -99 for t in teams}
    prev = {t: -99 for t in teams}
    away_last = {t: False for t in teams}
    streak = {t: 0 for t in teams}                       # consecutive games at the same venue type
    b2b = {t: 0 for t in teams}
    met: dict[tuple[int, int], int] = {}
    out: dict[int, list[tuple[int, int]]] = {}
    total_left = len(games)
    per_team = len(games) * 2 / len(teams)
    quota = B2B_SHARE * per_team                                  # ~15 per 82
    max_b2b = max(2, round(quota * 1.25))
    for i, d in enumerate(days):
        days_left = len(days) - i
        # Games tonight: the night's share of what's left, nudged by the weekday.
        target = caps[d] * total_left / max(1e-9, sum(caps[x] for x in days[i:]))
        n_tonight = int(target) + (1 if rng.random() < target - int(target) else 0)
        n_tonight = min(n_tonight, len(teams) // 2)
        if d in caps and caps[d] <= 0:
            continue

        def urgency(t):
            return left[t] / days_left

        cand = []
        for (h, a), k in pool.items():
            if k <= 0:
                continue
            score = 0.0
            ok = True
            for t in (h, a):
                if last[t] == d - 1 and prev[t] == d - 2:
                    ok = False                           # never three nights running
                    break
                if last[t] == d - 1:
                    if b2b[t] >= max_b2b and urgency(t) < 0.6:
                        ok = False
                        break
                    # Back-to-backs at the NBA's rate, spread through the season.
                    pace = quota * (per_team - left[t]) / per_team
                    score += B2B_NUDGE if b2b[t] < pace else -B2B_NUDGE
                score += urgency(t) * 2.0
            if not ok:
                continue
            if (away_last[a] and streak[a] >= MAX_ROAD_TRIP) or (not away_last[h] and streak[h] >= MAX_HOMESTAND
                                                                  and last[h] > -99):
                continue                                 # end the road trip / homestand
            if away_last[a]:
                score += 0.05                            # keep a road trip going
            if not away_last[h]:
                score += 0.03                            # ...and a homestand
            if d - met.get((min(h, a), max(h, a)), -99) <= 4:
                score -= 0.6                             # not the same two teams again so soon
            cand.append((score + rng.random() * 0.5, h, a))
        cand.sort(reverse=True)
        busy: set[int] = set()
        tonight = []
        # Teams that will run out of days unless they play tonight go first.
        must = {t for t in teams if left[t] > 0 and left[t] >= (days_left + 1) // 2 + 1}
        for pass_must in (True, False):
            for score, h, a in cand:
                if len(tonight) >= n_tonight and not (pass_must and (h in must or a in must)):
                    continue
                if pass_must and not (h in must or a in must):
                    continue
                if h in busy or a in busy or pool[(h, a)] <= 0:
                    continue
                tonight.append((h, a))
                busy |= {h, a}
                pool[(h, a)] -= 1
        for h, a in tonight:
            for t, away in ((h, False), (a, True)):
                if last[t] == d - 1:
                    b2b[t] += 1
                prev[t], last[t] = last[t], d
                left[t] -= 1
                streak[t] = streak[t] + 1 if (away == away_last[t] and streak[t] > 0) else 1
                away_last[t] = away
            met[(min(h, a), max(h, a))] = d
        total_left -= len(tonight)
        if tonight:
            out[d] = tonight
    leftover = [g for g, k in pool.items() for _ in range(k)]
    return out, leftover


def build_schedule(league: League, rng: random.Random, attempts: int = 12) -> None:
    """Replace this season's regular-season schedule with an NBA-shaped one."""
    teams = sorted(league.teams)
    if len(teams) < 2:
        return
    g_per = league.rules.games_per_season
    counts = matchup_counts(league, g_per, rng)
    games = _home_away(counts, rng)
    league.rules.games_per_season = 2 * len(games) // len(teams)
    cal = season_calendar(league.season, league.rules.games_per_season)
    blackout = set(cal["blackout"])
    base = season_start(league.season)
    days, caps = [], {}
    for d in range(cal["opening"], cal["end"] + 1):
        if d in blackout:
            continue
        load = WEEKDAY_LOAD[(base + timedelta(days=d)).weekday()]
        if d == cal["christmas"]:
            load = min(CHRISTMAS_GAMES, len(teams) // 2) / max(1.0, len(teams) / 4)
        days.append(d)
        caps[d] = load
    if cal["opening"] in caps:
        caps[cal["opening"]] = 0.35                     # opening night: a short marquee slate
    best = None
    for _ in range(attempts):
        placed, leftover = _place(games, teams, days, caps, random.Random(rng.random()))
        if cal["christmas"] in placed:
            limit = min(CHRISTMAS_GAMES, len(teams) // 2)
            extra = placed[cal["christmas"]][limit:]
            placed[cal["christmas"]] = placed[cal["christmas"]][:limit]
            leftover += extra
        if best is None or len(leftover) < len(best[1]):
            best = (placed, leftover)
        if not leftover:
            break
    placed, leftover = best
    # Anything that didn't fit goes on extra days after the end (rare; small leagues).
    d = cal["end"]
    while leftover:
        d += 1
        busy: set[int] = set()
        tonight = []
        for g in leftover[:]:
            if g[0] not in busy and g[1] not in busy:
                tonight.append(g)
                busy |= set(g)
                leftover.remove(g)
        placed[d] = tonight
    for gid in [g.id for g in league.games.values() if g.season == league.season and not g.playoff]:
        del league.games[gid]
    for d in sorted(placed):
        for h, a in placed[d]:
            league.add_game(Game(league.next_game_id(), league.season, d, h, a))


def rest_days(league: League, team_id: int, day: int) -> int:
    """Days since the team's previous game this season (99 if none): 1 = back-to-back."""
    prev = [g.day for g in league.games.values()
            if g.season == league.season and g.played and g.day < day
            and team_id in (g.home_team_id, g.away_team_id)]
    return day - max(prev) if prev else 99


def tired_from_last_night(league: League, team_id: int, day: int, minutes: float = 10.0) -> set[int]:
    """Players who played ``minutes`` or more the night before ``day`` (the second night
    of a back-to-back). Empty if the team rested."""
    for g in league.games.values():
        if (g.season == league.season and g.played and g.day == day - 1
                and team_id in (g.home_team_id, g.away_team_id)):
            box = g.box_score.home if g.home_team_id == team_id else g.box_score.away
            return {l.player_id for l in box.lines if l.seconds >= minutes * 60}
    return set()


def schedule_report(league: League) -> dict:
    """Per-team games, home games and back-to-backs, plus nights and their sizes (for checks)."""
    games = [g for g in league.games.values() if g.season == league.season and not g.playoff]
    per: dict[int, list[tuple[int, bool]]] = {}
    for g in games:
        per.setdefault(g.home_team_id, []).append((g.day, True))
        per.setdefault(g.away_team_id, []).append((g.day, False))
    teams = {}
    for t, lst in per.items():
        days = sorted(d for d, _ in lst)
        gaps = [b - a for a, b in zip(days, days[1:])]
        teams[t] = {"games": len(days), "home": sum(1 for _, h in lst if h),
                    "b2b": sum(1 for x in gaps if x == 1),
                    "three_in_three": sum(1 for a, b in zip(gaps, gaps[1:]) if a == 1 and b == 1),
                    "max_gap": max(gaps) if gaps else 0}
    nights: dict[int, int] = {}
    for g in games:
        nights[g.day] = nights.get(g.day, 0) + 1
    return {"teams": teams, "nights": nights}
