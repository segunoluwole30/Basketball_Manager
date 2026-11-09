"""Generate a league, save it, and print a summary.

    python -m hoopsim [--seed N] [--teams N] [--out league.db] [--team ABBR]
"""
from __future__ import annotations

import argparse

from . import generate_league, load_league, save_league
from .models import League, Player


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


def main() -> None:
    ap = argparse.ArgumentParser(prog="hoopsim")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--teams", type=int, default=12)
    ap.add_argument("--out", default="league.db")
    ap.add_argument("--team", help="team abbreviation to show (default: first team)")
    args = ap.parse_args()

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


if __name__ == "__main__":
    main()
