"""Command-line entry point.

    python -m hoopsim new    [--seed N] [--teams N] [--out league.db] [--team ABBR]
    python -m hoopsim game   [--seed N] [--pbp]          # one game: box score (+ play-by-play)
    python -m hoopsim season [--seed N] [--out league.db] # full season: standings and leaders
"""
from __future__ import annotations

import argparse
import random
from collections import defaultdict

from . import generate_league, load_league, save_league
from .engine import play_game, play_season
from .models import League, Player, TeamBox


def _money(n: int) -> str:
    return f"${n / 1_000_000:.1f}M"


def _player_row(p: Player, league: League, starter: bool, minutes: int | None) -> str:
    a = p.attributes
    inches = round(p.physique.height_cm / 2.54)
    c = p.contract
    number = "--" if p.jersey_number is None else str(p.jersey_number)
    return (f"{'*' if starter else ' '} #{number:<2} {p.name:<22} {p.position.value:<2} "
            f"{p.age(league.current_date):>2}y {inches // 12}'{inches % 12:<2} "
            f"CA {p.current_ability:>3} PA {p.potential_ability:>3}  "
            f"3PT {a.three_point:>2} FIN {a.finishing:>2} PAS {a.passing:>2} "
            f"DEF {max(a.perimeter_defense, a.interior_defense):>2} REB {a.defensive_rebounding:>2}  "
            f"{_money(c.salary) if c else '-':>6} to {c.expires_season if c else '-'}  "
            f"{minutes or 0:>2} mpg")


def cmd_new(args) -> None:
    league = generate_league(seed=args.seed, num_teams=args.teams)
    save_league(league, args.out)
    league = load_league(args.out)  # prove the save round-trips

    print(f"{league.name} {league.season}-{(league.season + 1) % 100:02d}: "
          f"{len(league.teams)} teams, {len(league.players)} players, "
          f"{len(league.games)} games ({league.rules.games_per_season} per team). Saved to {args.out}")
    problems = league.validate()
    print("Validation:", "OK" if not problems else problems)
    print()

    print(f"{'Team':<28}{'Conf':<10}{'Payroll':>9}{'Top-8 CA':>10}")
    for t in sorted(league.teams.values(), key=lambda t: (t.conference, t.division, t.city)):
        top8 = league.roster(t.id)[:8]
        avg = sum(p.current_ability for p in top8) / len(top8)
        print(f"{t.full_name:<28}{t.division:<10}{_money(league.payroll(t.id)):>9}{avg:>10.1f}")
    print()

    team = next((t for t in league.teams.values() if t.abbreviation == args.team),
                league.teams[min(league.teams)])
    tac = team.tactics
    print(f"{team.full_name} (pace {tac.pace}, 3PT rate {tac.three_point_rate}, "
          f"{tac.defensive_scheme.value} D)   * = starter")
    for p in league.roster(team.id):
        print(_player_row(p, league, p.id in tac.starters, tac.minutes_targets.get(p.id)))
    print()
    print("Best free agents:")
    for p in league.free_agents()[:5]:
        print(_player_row(p, league, False, None))


def print_box(league: League, box: TeamBox) -> None:
    team = league.teams[box.team_id]
    print(f"\n{team.full_name}")
    print(f"{'':<22}{'MIN':>4}{'PTS':>5}{'FG':>7}{'3PT':>7}{'FT':>7}{'REB':>5}{'AST':>5}"
          f"{'STL':>5}{'BLK':>5}{'TO':>4}{'PF':>4}{'+/-':>5}")

    def row(name: str, l, pm: bool = True) -> str:
        return (f"{name:<22}{l.minutes:>4.0f}{l.pts:>5}{f'{l.fgm}-{l.fga}':>7}{f'{l.tpm}-{l.tpa}':>7}"
                f"{f'{l.ftm}-{l.fta}':>7}{l.trb:>5}{l.ast:>5}{l.stl:>5}{l.blk:>5}{l.tov:>4}{l.pf:>4}"
                + (f"{l.plus_minus:>+5}" if pm else ""))

    for l in box.lines:
        if l.seconds == 0:
            continue
        p = league.players[l.player_id]
        name = f"{p.first_name[0]}. {p.last_name} {p.position.value}" + (" *" if l.starter else "")
        print(row(name, l))
    t = box.totals()
    print(row("TOTAL", t, pm=False))
    pct = lambda m, a: f"{m / a:.3f}" if a else "-"
    print(f"{'':<22}FG {pct(t.fgm, t.fga)}  3PT {pct(t.tpm, t.tpa)}  FT {pct(t.ftm, t.fta)}")


def cmd_game(args) -> None:
    league = generate_league(seed=args.seed)
    rng = random.Random(args.seed)
    game = league.games[1]
    sim = play_game(league, game.id, rng, record_pbp=args.pbp)
    home, away = league.teams[game.home_team_id], league.teams[game.away_team_id]
    if args.pbp:
        for e in sim.play_by_play:
            print(e.format())
        print()
    b = sim.box_score
    width = len(b.home.period_points)
    labels = [f"Q{i + 1}" if i < 4 else f"OT{i - 3}" for i in range(width)]
    print(f"{'':<6}" + "".join(f"{l:>5}" for l in labels) + f"{'T':>6}")
    for team, box in ((away, b.away), (home, b.home)):
        print(f"{team.abbreviation:<6}" + "".join(f"{p:>5}" for p in box.period_points) + f"{box.points:>6}")
    print_box(league, b.away)
    print_box(league, b.home)


def cmd_season(args) -> None:
    league = generate_league(seed=args.seed, num_teams=args.teams)
    play_season(league, random.Random(args.seed))
    if args.out:
        save_league(league, args.out)
        print(f"Saved to {args.out}\n")

    for conf in sorted({t.conference for t in league.teams.values()}):
        print(f"{conf:<26}{'W':>4}{'L':>4}{'PCT':>7}{'NET':>7}")
        for team, w, l in league.standings():
            if team.conference != conf:
                continue
            diffs = []
            for g in league.games.values():
                if team.id in (g.home_team_id, g.away_team_id):
                    d = g.result.home_score - g.result.away_score
                    diffs.append(d if g.home_team_id == team.id else -d)
            print(f"{team.full_name:<26}{w:>4}{l:>4}{w / (w + l):>7.3f}{sum(diffs) / len(diffs):>+7.1f}")
        print()

    totals = defaultdict(lambda: defaultdict(float))
    for g in league.games.values():
        for box in (g.box_score.home, g.box_score.away):
            for l in box.lines:
                t = totals[l.player_id]
                t["gp"] += l.seconds > 0
                t["min"] += l.minutes
                for f in ("pts", "trb", "ast", "stl", "blk", "tpm"):
                    t[f] += getattr(l, f)
    min_gp = league.rules.games_per_season * 0.6
    qualified = [pid for pid, t in totals.items() if t["gp"] >= min_gp]
    print("League leaders (per game)")
    for stat, label in (("pts", "Points"), ("trb", "Rebounds"), ("ast", "Assists"),
                        ("stl", "Steals"), ("blk", "Blocks"), ("tpm", "3PT made")):
        top = sorted(qualified, key=lambda pid: totals[pid][stat] / totals[pid]["gp"], reverse=True)[:3]
        cells = []
        for pid in top:
            p = league.players[pid]
            cells.append(f"{p.name} ({league.teams[p.team_id].abbreviation}) "
                         f"{totals[pid][stat] / totals[pid]['gp']:.1f}")
        print(f"  {label:<10}" + " | ".join(cells))


def main() -> None:
    ap = argparse.ArgumentParser(prog="hoopsim")
    sub = ap.add_subparsers(dest="cmd")

    new = sub.add_parser("new", help="generate and save a league")
    new.add_argument("--seed", type=int, default=0)
    new.add_argument("--teams", type=int, default=12)
    new.add_argument("--out", default="league.db")
    new.add_argument("--team", help="team abbreviation to show (default: first team)")

    game = sub.add_parser("game", help="simulate one game and print the box score")
    game.add_argument("--seed", type=int, default=0)
    game.add_argument("--pbp", action="store_true", help="print the play-by-play")

    season = sub.add_parser("season", help="simulate a full season")
    season.add_argument("--seed", type=int, default=0)
    season.add_argument("--teams", type=int, default=12)
    season.add_argument("--out", help="save the finished season to this file")

    args = ap.parse_args()
    if args.cmd is None:  # bare `python -m hoopsim` behaves like `new`
        args = ap.parse_args(["new"])
    {"new": cmd_new, "game": cmd_game, "season": cmd_season}[args.cmd](args)


if __name__ == "__main__":
    main()
