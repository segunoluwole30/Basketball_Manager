"""The playoff bracket as the web app draws it, and a playoff game's series context
(the viewer's scoreboard, box scores and game lists).

Every slot of the bracket is there from the start: play-in (7 v 8, 9 v 10 and the
decider for the 8 seed) per conference, then each conference's rounds and the
Finals. Slots whose series hasn't been drawn yet show the teams already known
(winners of finished feeder series) and "TBD" for the rest. Before the playoffs the
bracket is projected from the current standings."""
from __future__ import annotations

from typing import Callable, Optional

from ..models import Game, League, Phase, Series
from ..playoffs import _BRACKET, has_play_in, seeding, teams_per_conference

PLAY_IN_NOTE = {"7v8": "Winner is the 7 seed", "9v10": "Loser is out", "decider": "Winner is the 8 seed"}
PLAY_IN_MATCHUP = {"7v8": "7 v 8", "9v10": "9 v 10", "decider": "for the 8 seed"}


def conference_rounds(k: int) -> int:
    """Rounds inside a conference for a k-team bracket (8 -> 3)."""
    return max(1, k.bit_length() - 1)


def round_label(rnd: int, rounds: int, conference: Optional[str]) -> str:
    """'Play-in', 'First Round', 'Conf. Semifinals', 'Conf. Finals', 'Finals'."""
    if conference is None:
        return "Finals"
    if rnd == 0:
        return "Play-in"
    if rnd == rounds:
        return "Conference Finals"
    if rnd == rounds - 1:
        return "Conference Semifinals"
    return "First Round" if rnd == 1 else f"Round {rnd}"


def _rounds_for(league: League, conference: Optional[str]) -> int:
    if conference is None:
        return 0
    size = sum(1 for t in league.teams.values() if t.conference == conference)
    return conference_rounds(teams_per_conference(size))


def series_title(league: League, s: Series) -> str:
    """'East First Round', 'West Conference Finals', 'Finals', 'East Play-in'."""
    if s.conference is None:
        return "Finals"
    label = round_label(s.round, _rounds_for(league, s.conference), s.conference)
    return f"{s.conference} {label.replace('Conference ', '')}"


def series_status(a: str, a_wins: int, b: str, b_wins: int, need: int, play_in: str = "") -> str:
    """'BOS leads 2–0', 'Series tied 2–2', 'BOS wins 4–1'; play-in: 'BOS wins' or the stakes."""
    if play_in:
        if a_wins or b_wins:
            return f"{a if a_wins > b_wins else b} wins"
        return PLAY_IN_NOTE.get(play_in, "Play-in")
    hi, lo = max(a_wins, b_wins), min(a_wins, b_wins)
    if a_wins == b_wins:
        return f"Series tied {a_wins}–{b_wins}"
    leader = a if a_wins > b_wins else b
    return f"{leader} {'wins' if hi >= need else 'leads'} {hi}–{lo}"


def _series_of(league: League, game_id: int) -> tuple[Optional[Series], int]:
    for s in league.playoffs:
        if game_id in s.game_ids:
            return s, s.game_ids.index(game_id)
    return None, -1


def series_context(league: League, g: Game) -> Optional[dict]:
    """The series a playoff game belongs to, as it stood before tip-off (plus the
    score after it, for a played game). None for other games."""
    if not g.playoff or g.season != league.season:
        return None
    s, idx = _series_of(league, g.id)
    if s is None:
        return None
    wins = {s.high_id: 0, s.low_id: 0}
    for gid in s.game_ids[:idx]:
        prev = league.games.get(gid)
        if prev is not None and prev.played:
            wins[prev.winner_id()] = wins.get(prev.winner_id(), 0) + 1
    home, away = league.teams[g.home_team_id], league.teams[g.away_team_id]
    hw, aw = wins.get(home.id, 0), wins.get(away.id, 0)
    out = {"series_id": s.id, "title": series_title(league, s), "round": s.round,
           "conference": s.conference, "play_in": s.play_in, "game": idx + 1,
           "best_of": s.best_of, "wins_needed": s.wins_needed,
           "home": home.abbreviation, "away": away.abbreviation,
           "home_wins": hw, "away_wins": aw,
           "status": series_status(home.abbreviation, hw, away.abbreviation, aw, s.wins_needed, s.play_in)}
    if s.play_in:
        out["matchup"] = PLAY_IN_MATCHUP.get(s.play_in, "")
    if g.played:
        w = g.winner_id()
        hw2, aw2 = hw + (w == home.id), aw + (w == away.id)
        out["status_after"] = series_status(home.abbreviation, hw2, away.abbreviation, aw2,
                                            s.wins_needed, s.play_in)
    return out


def viewer_series(league: League, g: Game) -> Optional[dict]:
    """What the match viewer's scoreboard gets: the series before the game. The
    result after it is worked out by the page at the final buzzer (no spoilers)."""
    ctx = series_context(league, g)
    if ctx is None:
        return None
    ctx.pop("status_after", None)
    return ctx


# ---------------------------------------------------------------- the bracket
def _team(league: League, tid: Optional[int], seed: Optional[int], label: str = "TBD") -> dict:
    if tid is None:
        return {"id": None, "abbr": label, "name": label, "seed": seed}
    t = league.teams[tid]
    return {"id": tid, "abbr": t.abbreviation, "name": t.full_name, "seed": seed}


def _seed(s: Series, tid: int) -> int:
    return s.high_seed if tid == s.high_id else s.low_seed


class _Builder:
    def __init__(self, league: League, game_view: Callable[[Game], dict], projected: bool):
        self.lg = league
        self.game_view = game_view
        self.projected = projected
        self.series = list(league.playoffs) if not projected else []

    def slot(self, rnd: int, conf: Optional[str], top: dict, bottom: dict, s: Optional[Series] = None,
             play_in: str = "", rounds: int = 0) -> dict:
        title = round_label(rnd, rounds, conf)
        out = {"id": None, "round": rnd, "conference": conf, "label": title, "play_in": play_in,
               "teams": [top, bottom], "wins": [0, 0], "over": False, "winner_id": None,
               "best_of": 1 if play_in else self.lg.rules.playoff_series_length,
               "games": [], "status": "", "projected": self.projected}
        if play_in:
            out["matchup"] = PLAY_IN_MATCHUP[play_in]
            out["status"] = PLAY_IN_NOTE[play_in]
        if s is not None:
            wins = {s.high_id: s.high_wins, s.low_id: s.low_wins}
            a, b = top["id"], bottom["id"]
            out.update({"id": s.id, "best_of": s.best_of, "wins": [wins.get(a, 0), wins.get(b, 0)],
                        "over": s.over, "winner_id": s.winner_id,
                        "games": [self.game_view(self.lg.games[g]) for g in s.game_ids if g in self.lg.games],
                        "status": series_status(top["abbr"], wins.get(a, 0), bottom["abbr"], wins.get(b, 0),
                                                s.wins_needed, s.play_in)
                        if (s.high_wins or s.low_wins or not s.play_in) else out["status"]})
        return out

    def from_series(self, s: Series, rounds: int, top_id: Optional[int] = None) -> dict:
        a, b = (s.high_id, s.low_id) if top_id in (None, s.high_id) else (s.low_id, s.high_id)
        return self.slot(s.round, s.conference, _team(self.lg, a, _seed(s, a)), _team(self.lg, b, _seed(s, b)),
                         s, s.play_in, rounds)

    @staticmethod
    def winner(slot: dict, label: str) -> dict:
        if slot["over"]:
            return next(t for t in slot["teams"] if t["id"] == slot["winner_id"])
        return {"id": None, "abbr": "TBD", "name": label, "seed": None}

    def conference(self, conf: str, order: list[int]) -> dict:
        lg = self.lg
        k = teams_per_conference(len(order))
        rounds = conference_rounds(k)
        mine = [s for s in self.series if s.conference == conf]
        play_in: list[dict] = []
        seed_team: dict[int, dict] = {i + 1: _team(lg, order[i], i + 1) for i in range(k)}
        if has_play_in(lg, len(order)):
            by = {s.play_in: s for s in mine if s.play_in}
            for key, (x, y) in (("7v8", (k - 1, k)), ("9v10", (k + 1, k + 2))):
                if key in by:
                    play_in.append(self.from_series(by[key], rounds))
                else:
                    play_in.append(self.slot(0, conf, _team(lg, order[x - 1], x), _team(lg, order[y - 1], y),
                                             play_in=key, rounds=rounds))
            s78, s910 = play_in
            if "decider" in by:
                play_in.append(self.from_series(by["decider"], rounds))
            else:
                if s78["over"]:
                    lose = next(t for t in s78["teams"] if t["id"] != s78["winner_id"])
                else:
                    lose = {"id": None, "abbr": "TBD", "name": "Loser of 7 v 8", "seed": None}
                play_in.append(self.slot(0, conf, lose, self.winner(s910, "Winner of 9 v 10"), play_in="decider",
                                         rounds=rounds))
            seed_team[k - 1] = dict(self.winner(s78, "Winner of 7 v 8"), seed=k - 1)
            seed_team[k] = dict(self.winner(play_in[2], "Play-in winner"), seed=k)
        cols: list[list[dict]] = []
        for rnd in range(1, rounds + 1):
            drawn = sorted((s for s in mine if s.round == rnd), key=lambda s: s.id)
            col = []
            n = k >> rnd
            for j in range(n):
                if rnd == 1:
                    hi, lo = _BRACKET[k][2 * j], _BRACKET[k][2 * j + 1]
                    top, bottom = seed_team[hi], seed_team[lo]
                else:
                    top = self.winner(cols[-1][2 * j], "TBD")
                    bottom = self.winner(cols[-1][2 * j + 1], "TBD")
                if j < len(drawn):
                    col.append(self.from_series(drawn[j], rounds, top["id"]))
                else:
                    col.append(self.slot(rnd, conf, top, bottom, rounds=rounds))
            cols.append(col)
        return {"conference": conf, "play_in": play_in, "rounds": cols,
                "round_labels": [round_label(r, rounds, conf) for r in range(1, rounds + 1)]}


def bracket(league: League, game_view: Callable[[Game], dict]) -> Optional[dict]:
    """The whole bracket (see the module docstring), or None when there is nothing
    to draw (no games played yet, or the season is over)."""
    lg = league
    projected = lg.phase is Phase.REGULAR_SEASON
    if projected and not any(g.played for g in lg.games.values() if g.season == lg.season and not g.playoff):
        return None
    if not projected and lg.phase is not Phase.PLAYOFFS and not lg.playoffs:
        return None
    b = _Builder(lg, game_view, projected)
    confs = [b.conference(c, order) for c, order in seeding(lg).items()]
    finals = None
    if len(confs) >= 2:
        fs = next((s for s in b.series if s.conference is None), None)
        champs = [b.winner(c["rounds"][-1][0], "TBD") for c in confs[:2]]
        if fs is not None:
            finals = b.from_series(fs, 0, champs[0]["id"] if champs[0]["id"] in (fs.high_id, fs.low_id) else None)
            seeds = {c["id"]: c["seed"] for c in champs}       # conference seeds, not the Finals' 1/2
            for t in finals["teams"]:
                t["seed"] = seeds.get(t["id"], t["seed"])
        else:
            finals = b.slot(len(confs[0]["rounds"]) + 1, None, dict(champs[0]), dict(champs[1]))
    return {"projected": projected, "conferences": confs, "finals": finals}
