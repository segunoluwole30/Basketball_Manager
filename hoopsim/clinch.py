"""Clinch and elimination markers for the standings (x, y, z, w, pi, o).

A marker is shown only once it is mathematically certain, given every team's
remaining schedule (who plays whom), and it is shown as soon as this module can
prove it, not just at the end of the season.

**Clinching place k in a group** (a conference, a division or the league) for
team T: let T lose every remaining game. If even then no completion of the other
games puts k other teams of the group ahead of T, T is certain to finish in the
top k. Losing more can only hurt T (each loss is a win for someone else), so this
is the worst case for T. The adversary needs a set S of k rivals that can *all*
reach the wins needed to pass T at the same time. For a given S that is a
max-flow question, like the classic baseball-elimination problem: each S member
gets every remaining game against teams outside S for free, and the games inside
S (which two S members can't both win) are distributed by the flow.

**Eliminated from place k**: let T win every remaining game. T is eliminated if no
completion keeps at least ``len(group) - k`` rivals behind it. Keeping a set R of
rivals behind T is again a max flow: R members lose every game against teams
outside R, and the games inside R are spread so nobody passes T.

Both families of sets are closed under taking subsets (a smaller S gets more free
wins, a smaller R has fewer games to absorb), so a depth-first search that drops
infeasible sets finds a witness or proves there is none. Its work is capped; when
the cap is hit the answer is "not proven", so a marker can appear later than a
perfect solver would show it, but never wrongly.

**Ties.** The league breaks ties with the NBA procedure (``playoffs._Tiebreak``),
which can depend on games not yet played and, in the end, on point differential.
So a tie is counted *against* T when clinching and *for* T when eliminating
("pessimistic" checks P below). One case is settled exactly: when at most one rival
can still finish level with T, any tie is a two-team tie, whose first step is the
season series; with T losing (or winning) all its remaining games the season series
against each rival is already fixed, so a series T has already won (lost) counts as
a decided tie (checks Q). If a tie could involve three or more teams, or the season
series is split, it stays pessimistic.

At the end of the regular season the standings are final and the ranks are used
directly.

The "wins to clinch" and "losses to elimination" numbers are sound upper bounds:
win that many more (or lose that many more) and the spot is clinched (or lost)
whatever else happens. They are computed with the same checks, letting T's rivals
still win all their games against T (or lose them all), which can't happen
together with T's own results; that only makes the bounds safer.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

from .models import League
from .playoffs import _Tiebreak, ranked, team_records, teams_per_conference

MARKERS = {
    "w": "Clinched the best record in the league",
    "z": "Clinched the conference's #1 seed",
    "y": "Clinched the division",
    "x": "Clinched a playoff spot (top 6, avoiding the play-in)",
    "pi": "Clinched at least a play-in spot (top 10)",
    "o": "Eliminated from playoff and play-in contention",
}
MARKER_ORDER = ("w", "z", "y", "x", "pi", "o")
SEARCH_BUDGET = 1500        # feasibility checks per question before giving up (= "not proven")
DETAIL_BUDGET = 60          # the same for the wins-to-clinch / losses-to-elimination numbers


def race_format(conference_size: int, play_in: bool = True) -> dict:
    """Which places the markers are about, for a conference of this size.

    Conferences that play a play-in (an 8-team bracket and at least 10 teams, as in
    ``playoffs.has_play_in``) use the NBA's format: seeds 1-6 go straight in, 7-10 to
    the play-in. Smaller leagues, or ``rules.play_in`` off, have none: x is a bracket
    place and o is missing the bracket."""
    bracket = teams_per_conference(conference_size)
    if play_in and bracket == 8 and conference_size >= 10:
        return {"direct": 6, "play_in": 10, "bracket": 8}
    return {"direct": bracket, "play_in": None, "bracket": bracket}


# -- the facts the checks need -------------------------------------------------------
class _Ctx:
    def __init__(self, league: League):
        self.lg = league
        teams = list(league.teams)
        self.wins = {t: 0 for t in teams}
        self.losses = {t: 0 for t in teams}
        self.rem = {t: 0 for t in teams}
        self.rem_vs: dict[int, dict[int, int]] = {t: {} for t in teams}
        self.h2h_w: dict[tuple[int, int], int] = {}      # (a, b) -> a's wins over b so far
        self.h2h_g: dict[tuple[int, int], int] = {}      # (a, b) -> all scheduled games
        for g in league.games.values():
            if g.season != league.season or g.playoff:
                continue
            h, a = g.home_team_id, g.away_team_id
            for x, y in ((h, a), (a, h)):
                self.h2h_g[(x, y)] = self.h2h_g.get((x, y), 0) + 1
            if g.played:
                w = g.winner_id()
                l = a if w == h else h
                self.wins[w] += 1
                self.losses[l] += 1
                self.h2h_w[(w, l)] = self.h2h_w.get((w, l), 0) + 1
            else:
                self.rem[h] += 1
                self.rem[a] += 1
                self.rem_vs[h][a] = self.rem_vs[h].get(a, 0) + 1
                self.rem_vs[a][h] = self.rem_vs[a].get(h, 0) + 1
        self.n = {t: self.wins[t] + self.losses[t] + self.rem[t] for t in teams}
        self.budget = 0
        self.limit = SEARCH_BUDGET

    def hw(self, a: int, b: int) -> int:
        return self.h2h_w.get((a, b), 0)

    def hg(self, a: int, b: int) -> int:
        return self.h2h_g.get((a, b), 0)

    def vs(self, a: int, b: int) -> int:
        return self.rem_vs[a].get(b, 0)


# -- max flow on a tiny bipartite graph ------------------------------------------------
def _max_flow(pairs: list[tuple[int, int, int]], caps: dict[int, int]) -> int:
    """Games (a, b, count) -> teams (capacity caps[t]): the most games that can be won
    without any team going over its capacity.

    A max flow source -> games -> teams -> sink, run on the teams alone: a greedy
    first pass, then shortest augmenting paths where team t can pass one of its wins
    over u back to u. That is the residual graph of the full network (the game nodes
    only ever sit between their two teams), so the result is the exact max flow."""
    load = dict.fromkeys(caps, 0)
    held: dict[int, dict[int, int]] = {t: {} for t in caps}   # held[t][u]: t's wins over u
    free = []                                                  # [a, b, units not yet won]
    for a, b, c in pairs:
        for x, y in ((a, b), (b, a)) if caps[a] - load[a] >= caps[b] - load[b] else ((b, a), (a, b)):
            give = min(c, max(0, caps[x] - load[x]))
            if give:
                load[x] += give
                held[x][y] = held[x].get(y, 0) + give
                c -= give
        if c:
            free.append([a, b, c])
    flow = sum(load.values())
    while free:
        # multi-source BFS from the teams of every game with units left
        parent: dict[int, Optional[int]] = {}
        origin: dict[int, int] = {}
        q = deque()
        for i, (a, b, c) in enumerate(free):
            for x in (a, b):
                if x not in parent:
                    parent[x] = None
                    origin[x] = i
                    q.append(x)
        end = None
        while q:
            t = q.popleft()
            if load[t] < caps[t]:
                end = t
                break
            for u, n in held[t].items():
                if n > 0 and u not in parent:
                    parent[u] = t
                    origin[u] = origin[t]
                    q.append(u)
        if end is None:
            break
        i = origin[end]
        push = caps[end] - load[end]
        v = end
        while parent[v] is not None:
            push = min(push, held[parent[v]][v])
            v = parent[v]
        start = v
        push = min(push, free[i][2])
        v = end
        while parent[v] is not None:
            u = parent[v]
            held[u][v] -= push
            held[v][u] = held[v].get(u, 0) + push
            v = u
        a, b, _ = free[i]
        other = b if start == a else a
        held[start][other] = held[start].get(other, 0) + push
        load[end] += push
        flow += push
        free[i][2] -= push
        if not free[i][2]:
            free.pop(i)
    return flow


def _intra(ctx: _Ctx, members) -> list[tuple[int, int, int]]:
    ms = list(members)
    out = []
    for i, a in enumerate(ms):
        for b in ms[i + 1:]:
            c = ctx.vs(a, b)
            if c:
                out.append((a, b, c))
    return out


def _can_all_reach(ctx: _Ctx, S, need: dict[int, int]) -> bool:
    """Can every team in S reach ``need`` wins at once? Games against teams outside S
    are free wins; games inside S are shared out by the flow."""
    ctx.budget -= 1
    deficit = {}
    for x in S:
        free = ctx.rem[x] - sum(ctx.vs(x, y) for y in S if y != x)
        deficit[x] = max(0, need[x] - ctx.wins[x] - free)
    total = sum(deficit.values())
    if total == 0:
        return True
    return _max_flow(_intra(ctx, S), deficit) == total


def _can_all_stay(ctx: _Ctx, R, cap: dict[int, int]) -> bool:
    """Can every team in R stay at or below ``cap`` wins at once? They lose every game
    against teams outside R; the games inside R must all be won by someone in R."""
    ctx.budget -= 1
    room = {x: cap[x] - ctx.wins[x] for x in R}
    if any(v < 0 for v in room.values()):
        return False
    pairs = _intra(ctx, R)
    total = sum(c for _, _, c in pairs)
    return total == 0 or _max_flow(pairs, room) == total


def _find_set(ctx: _Ctx, cands: list[int], size: int, feasible) -> Optional[bool]:
    """Is there a feasible subset of ``cands`` with ``size`` members? The feasible sets
    are closed under subsets. None when the search budget runs out."""
    if size <= 0:
        return True
    if len(cands) < size:
        return False
    if size == 1:
        return True                     # candidates are individually feasible
    if feasible(cands):
        return True
    ctx.budget = ctx.limit
    out_of_budget = False

    def dfs(chosen: list[int], start: int) -> bool:
        nonlocal out_of_budget
        if len(chosen) == size:
            return True
        for i in range(start, len(cands)):
            if len(chosen) + (len(cands) - i) < size:
                return False
            if ctx.budget <= 0:
                out_of_budget = True
                return False
            trial = chosen + [cands[i]]
            if len(trial) >= 2 and not feasible(trial):
                continue
            if dfs(trial, i + 1):
                return True
            if out_of_budget:
                return False
        return False

    found = dfs([], 0)
    if found:
        return True
    return None if out_of_budget else False


# -- the two questions -----------------------------------------------------------------
def _tie_value(W: int, nT: int, nX: int) -> Optional[int]:
    """X's win total that is exactly level with T's W wins (same win %), if any."""
    return W * nX // nT if (W * nX) % nT == 0 else None


def clinched(ctx: _Ctx, T: int, group, k: int, extra_wins: int = 0) -> bool:
    """Is T certain to finish in the top k of ``group`` (if it wins ``extra_wins`` more
    and loses the rest)?"""
    others = [x for x in group if x != T]
    if len(others) < k:
        return True
    W, nT = ctx.wins[T] + extra_wins, ctx.n[T]
    need_tie, need_strict, lo, hi = {}, {}, {}, {}
    for x in others:
        nX = ctx.n[x]
        need_tie[x] = -(-W * nX // nT)          # ceil: level with T or better
        need_strict[x] = W * nX // nT + 1       # strictly better than T
        lo[x] = ctx.wins[x] + ctx.vs(x, T)
        hi[x] = ctx.wins[x] + ctx.rem[x]
    # P: every tie goes against T.
    cands = sorted((x for x in others if hi[x] >= need_tie[x]), key=lambda x: need_tie[x] - lo[x])
    res = _find_set(ctx, cands, k, lambda S: _can_all_reach(ctx, S, need_tie))
    if res is False:
        return True
    if extra_wins:
        return False        # Q is only exact for T's real worst case (see the module doc)
    # Q: a season series T has already won counts as a decided two-team tie.
    decided = {x for x in others if ctx.hg(T, x) and 2 * ctx.hw(T, x) > ctx.hg(T, x)}
    need_q = {x: need_strict[x] if x in decided else need_tie[x] for x in others}
    cands_q = sorted((x for x in others if hi[x] >= need_q[x]), key=lambda x: need_q[x] - lo[x])
    res_q = _find_set(ctx, cands_q, k, lambda S: _can_all_reach(ctx, S, need_q))
    if res_q is not False:
        return False
    # Q holds; it's exact only if no tie with T can involve three or more teams.
    level = 0
    for x in others:
        v = _tie_value(W, nT, ctx.n[x])
        level += v is not None and lo[x] <= v <= hi[x]
    return level <= 1


def eliminated(ctx: _Ctx, T: int, group, k: int, extra_losses: int = 0) -> bool:
    """Is T certain to finish outside the top k of ``group`` (if it loses
    ``extra_losses`` more and wins the rest)?"""
    others = [x for x in group if x != T]
    if len(others) < k:
        return False
    W, nT = ctx.wins[T] + ctx.rem[T] - extra_losses, ctx.n[T]
    hold = len(group) - k                       # rivals that must stay behind T
    cap_tie, cap_strict, lo, hi = {}, {}, {}, {}
    for x in others:
        nX = ctx.n[x]
        cap_tie[x] = W * nX // nT               # level with T at most
        cap_strict[x] = -(-W * nX // nT) - 1    # strictly behind T
        lo[x] = ctx.wins[x]
        hi[x] = ctx.wins[x] + ctx.rem[x] - ctx.vs(x, T)
    # P: every tie goes T's way.
    cands = sorted((x for x in others if lo[x] <= cap_tie[x]), key=lambda x: lo[x] - cap_tie[x])
    res = _find_set(ctx, cands, hold, lambda R: _can_all_stay(ctx, R, cap_tie))
    if res is False:
        return True         # even winning every tie isn't enough
    if extra_losses:
        return False        # Q is only exact for T's real best case (see the module doc)
    # Q: a season series T has already lost counts as a decided two-team tie.
    decided = {x for x in others
               if ctx.hg(T, x) and 2 * (ctx.hw(T, x) + ctx.vs(T, x)) < ctx.hg(T, x)}
    cap_q = {x: cap_strict[x] if x in decided else cap_tie[x] for x in others}
    cands_q = sorted((x for x in others if lo[x] <= cap_q[x]), key=lambda x: lo[x] - cap_q[x])
    res_q = _find_set(ctx, cands_q, hold, lambda R: _can_all_stay(ctx, R, cap_q))
    if res_q is not False:
        return False
    level = 0
    for x in others:
        v = _tie_value(W, nT, ctx.n[x])
        level += v is not None and lo[x] <= v <= hi[x]
    return level <= 1


def _smallest(pred, hi: int) -> Optional[int]:
    """Smallest j in [0, hi] with pred(j) true, assuming it's (roughly) monotone; the
    returned j always satisfies pred. None if pred(hi) is false."""
    if pred(0):
        return 0
    if hi <= 0 or not pred(hi):
        return None
    lo_bad, good = 0, hi
    while good - lo_bad > 1:
        mid = (lo_bad + good) // 2
        if pred(mid):
            good = mid
        else:
            lo_bad = mid
    return good


# -- the standings' view -----------------------------------------------------------------
def _races(league: League, tid: int) -> list[tuple]:
    """(key, label, group kind, group, places, marker if clinched, marker if lost)."""
    T = league.teams
    team = T[tid]
    conf = [x for x in T if T[x].conference == team.conference]
    div = [x for x in T if T[x].division == team.division]
    fmt = race_format(len(conf), bool(getattr(league.rules, "play_in", True)))
    races = []
    if len({t.conference for t in T.values()}) > 1:
        races.append(("league", "Best record in the league", "league", list(T), 1, "w", None))
    races.append(("seed1", "Conference #1 seed", "conf", conf, 1, "z", None))
    if 1 < len(div) < len(conf):
        races.append(("division", "Division title", "div", div, 1, "y", None))
    if fmt["play_in"]:
        races.append(("playoffs", "Top 6: playoffs without the play-in", "conf", conf, fmt["direct"], "x", None))
        races.append(("play_in", "Top 10: play-in or better", "conf", conf, fmt["play_in"], "pi", "o"))
    else:
        races.append(("playoffs", f"Top {fmt['direct']}: playoffs", "conf", conf, fmt["direct"], "x", "o"))
    return races


def _final_ranks(league: League) -> dict:
    T = league.teams
    tb = _Tiebreak(league)
    out = {}
    for kind, attr in (("conf", "conference"), ("div", "division")):
        for name in {getattr(t, attr) for t in T.values()}:
            for i, t in enumerate(tb.order([x for x in T if getattr(T[x], attr) == name])):
                out[(kind, t)] = i + 1
    for i, t in enumerate(tb.order(list(T))):
        out[("league", t)] = i + 1
    return out


def race(league: League) -> dict[int, dict]:
    """{team id: {"marker": the strongest, "markers": all, in MARKER_ORDER}} for the
    current regular season."""
    ctx = _Ctx(league)
    final = _final_ranks(league) if not any(ctx.rem.values()) else None
    out = {}
    for tid in league.teams:
        markers = []
        for key, label, kind, group, k, m_in, m_out in _races(league, tid):
            if final is not None:
                got = final[(kind, tid)] <= k
                lost = not got
            else:
                got = bool(m_in) and clinched(ctx, tid, group, k)
                lost = bool(m_out) and not got and eliminated(ctx, tid, group, k)
            if got and m_in:
                markers.append(m_in)
            if lost and m_out:
                markers.append(m_out)
        markers.sort(key=MARKER_ORDER.index)
        out[tid] = {"marker": markers[0] if markers else "", "markers": markers}
    return out


def race_details(league: League, tid: int) -> list[dict]:
    """Per race (best record, #1 seed, division, top 6/8/10): clinched, eliminated, or
    the wins that clinch it and the losses that end it, whatever else happens."""
    ctx = _Ctx(league)
    final = _final_ranks(league) if not any(ctx.rem.values()) else None
    rows = []
    for key, label, kind, group, k, m_in, m_out in _races(league, tid):
        row = {"key": key, "label": label, "places": k, "marker": m_in, "clinched": False,
               "eliminated": False, "wins_to_clinch": None, "losses_to_elimination": None}
        if final is not None:
            row["clinched"] = final[(kind, tid)] <= k
            row["eliminated"] = not row["clinched"]
        else:
            row["clinched"] = clinched(ctx, tid, group, k)
            row["eliminated"] = not row["clinched"] and eliminated(ctx, tid, group, k)
            if not row["clinched"] and not row["eliminated"]:
                ctx.limit = DETAIL_BUDGET
                row["wins_to_clinch"] = _smallest(lambda j: clinched(ctx, tid, group, k, j), ctx.rem[tid])
                row["losses_to_elimination"] = _smallest(lambda j: eliminated(ctx, tid, group, k, j),
                                                         ctx.rem[tid])
                ctx.limit = SEARCH_BUDGET
        rows.append(row)
    return rows


def marker_legend(league: League) -> list[dict]:
    """The markers that can appear in this league, with what they mean."""
    can = set()
    no_play_in = None
    for tid in league.teams:
        for key, _, _, _, k, m_in, m_out in _races(league, tid):
            can |= {m_in, m_out} - {None}
            if key == "playoffs" and m_out:
                no_play_in = k
    out = []
    for m in MARKER_ORDER:
        if m in can:
            text = MARKERS[m]
            if no_play_in and m == "x":
                text = f"Clinched a playoff spot (top {no_play_in})"
            if no_play_in and m == "o":
                text = "Eliminated from playoff contention"
            out.append({"marker": m, "meaning": text})
    return out


def _phrase(league: League, tid: int, marker: str) -> str:
    t = league.teams[tid]
    conf_size = sum(1 for x in league.teams.values() if x.conference == t.conference)
    fmt = race_format(conf_size, bool(getattr(league.rules, "play_in", True)))
    return {
        "w": "clinched the best record in the league",
        "z": f"clinched the {t.conference}'s #1 seed",
        "y": f"clinched the {t.division} Division",
        "x": "clinched a playoff spot" + (" (top 6)" if fmt["play_in"] else ""),
        "pi": "clinched at least a play-in spot",
        "o": "were eliminated from " + ("playoff and play-in contention" if fmt["play_in"] else "playoff contention"),
    }[marker]


def race_changes(league: League, before: dict[int, dict], after: dict[int, dict]) -> list[dict]:
    """Markers in ``after`` that weren't in ``before`` (both from ``race``), best first:
    [{"team_id", "marker", "text"}] with text like "The Harbor City Mariners clinched a
    playoff spot (top 6)"."""
    out = []
    for tid, now in after.items():
        old = set(before.get(tid, {}).get("markers", []))
        for m in now["markers"]:
            if m not in old:
                out.append({"team_id": tid, "marker": m,
                            "text": f"The {league.teams[tid].full_name} {_phrase(league, tid, m)}"})
    out.sort(key=lambda c: (MARKER_ORDER.index(c["marker"]), league.teams[c["team_id"]].full_name))
    return out


def standings_lines(league: League, me: Optional[int] = None) -> list[str]:
    """Conference standings with clinch / elimination markers, GB, L10 and streak."""
    rec = team_records(league)
    marks = race(league)
    form = recent_form(league)
    out = []
    for conf in sorted({t.conference for t in league.teams.values()}):
        ids = ranked(league, [t.id for t in league.teams.values() if t.conference == conf])
        lw, ll, _ = rec[ids[0]]
        out.append(f"    {conf:<26}{'W':>4}{'L':>4}{'PCT':>7}{'GB':>6}{'NET':>7}{'L10':>6}{'STRK':>6}")
        for i, tid in enumerate(ids):
            w, l, diff = rec[tid]
            gb = ((lw - w) + (l - ll)) / 2
            last10, streak = form[tid]
            out.append(f"{marks[tid]['marker']:>2}  {league.teams[tid].full_name:<26}{w:>4}{l:>4}"
                       f"{w / max(1, w + l):>7.3f}{(f'{gb:.1f}' if gb else '-'):>6}"
                       f"{diff / max(1, w + l):>+7.1f}{last10:>6}{streak:>6}" + (" <--" if tid == me else ""))
        out.append("")
    out.append("  ".join(f"{m['marker']}: {m['meaning'][0].lower() + m['meaning'][1:]}" for m in marker_legend(league)))
    return out


def recent_form(league: League) -> dict[int, tuple[str, str]]:
    """{team id: (last-10 record, streak)} for the regular season so far."""
    games = sorted((g for g in league.games.values() if g.season == league.season and not g.playoff and g.played),
                   key=lambda g: (g.day, g.id))
    results: dict[int, list[str]] = {t: [] for t in league.teams}
    for g in games:
        w = g.winner_id()
        results[g.home_team_id].append("W" if w == g.home_team_id else "L")
        results[g.away_team_id].append("W" if w == g.away_team_id else "L")
    out = {}
    for tid, r in results.items():
        last = r[-10:]
        n = len(r) - len("".join(r).rstrip(r[-1])) if r else 0
        out[tid] = (f"{last.count('W')}-{last.count('L')}", f"{r[-1]}{n}" if r else "-")
    return out
