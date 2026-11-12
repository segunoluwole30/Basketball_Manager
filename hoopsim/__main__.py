"""Command-line entry point.

    python -m hoopsim new    [--seed N] [--teams N] [--out league.db] [--team ABBR]
    python -m hoopsim game   [--seed N] [--pbp]          # one game: box score (+ play-by-play)
    python -m hoopsim season [--seed N] [--out league.db] # season + playoffs, awards, leaders
    python -m hoopsim career [--seasons 10] [--seed N] [--load league.db] [--out league.db]
    python -m hoopsim play   [--team ABBR] [--load save.db]   # manage a team yourself
    python -m hoopsim watch  [--home ABBR --away ABBR] [--possessions 40]   # spatial prototype in a browser
    python -m hoopsim watch --full [--quarter 4]   # a full spatial game (or one quarter of it)
    python -m hoopsim web    [--load save.db] [--port 8000]   # play as GM in your browser
"""
from __future__ import annotations

import argparse
import random

from . import generate_league, load_league, save_league
from .engine import play_game
from .models import League, Player, TeamBox
from .playoffs import ranked, team_records
from .season import play_full_season, run_offseason
from .stats import career_per_game, leaders, player_label, totals_from_careers


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


def set_engine(league, args) -> None:
    """``--engine``: which match engine the league's games are played on."""
    if getattr(args, "engine", None):
        league.rules.match_engine = args.engine


def make_league(args):
    """``--league FILE``: start from a league file; otherwise generate one."""
    if getattr(args, "league", None):
        from .league_file import LeagueFileError, load_league_file
        try:
            return load_league_file(args.league, seed=args.seed or 0)
        except (OSError, LeagueFileError) as e:
            raise SystemExit(f"can't load {args.league}: {e}")
    return generate_league(seed=args.seed, num_teams=args.teams)


def cmd_new(args) -> None:
    league = make_league(args)
    set_engine(league, args)
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
    set_engine(league, args)
    rng = random.Random(args.seed)
    game = league.games[1]
    sim = play_game(league, game.id, rng, record_pbp=args.pbp)
    home, away = league.teams[game.home_team_id], league.teams[game.away_team_id]
    if args.pbp and league.plays_spatial(game):
        print("(spatial games record movement, not text: watch one with `python -m hoopsim watch --full`)\n")
    elif args.pbp:
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


def print_standings(league: League) -> None:
    rec = team_records(league)
    for conf in sorted({t.conference for t in league.teams.values()}):
        print(f"{conf:<26}{'W':>4}{'L':>4}{'PCT':>7}{'NET':>7}")
        for tid in ranked(league, [t.id for t in league.teams.values() if t.conference == conf]):
            w, l, diff = rec[tid]
            print(f"{league.teams[tid].full_name:<26}{w:>4}{l:>4}{w / max(1, w + l):>7.3f}"
                  f"{diff / max(1, w + l):>+7.1f}")
        print()


def print_playoffs(league: League) -> None:
    names = {1: "First round", 2: "Second round", 3: "Third round", 4: "Fourth round"}
    for s in sorted(league.playoffs, key=lambda s: (s.round, s.id)):
        stage = "Finals" if s.conference is None else f"{s.conference} {names.get(s.round, 'round ' + str(s.round))}"
        hi, lo = league.teams[s.high_id].abbreviation, league.teams[s.low_id].abbreviation
        print(f"  {stage:<22} ({s.high_seed}) {hi} {s.high_wins}-{s.low_wins} {lo} ({s.low_seed})")


def print_awards(league: League, summary: dict) -> None:
    labels = {"mvp": "MVP", "finals_mvp": "Finals MVP", "dpoy": "Defensive POY",
              "roy": "Rookie of the Year", "sixth_man": "Sixth Man", "mip": "Most Improved"}
    for key, label in labels.items():
        print(f"  {label:<20}{player_label(league, summary['awards'].get(key))}")
    a = summary.get("all_star")
    if a:
        teams = ", ".join(f"{k} {v}" for k, v in a["score"].items())
        print(f"  {'All-Star Game':<20}{teams}; MVP {player_label(league, a['mvp'])}")
        if a.get("three_point_champion") is not None:
            print(f"  {'3-Point Contest':<20}{player_label(league, a['three_point_champion'])}")
        if a.get("rising_stars_mvp") is not None:
            print(f"  {'Rising Stars MVP':<20}{player_label(league, a['rising_stars_mvp'])}")


def print_leaders(league: League, season: int) -> None:
    totals = totals_from_careers(league, season)
    print("League leaders (per game)")
    for stat, label in (("pts", "Points"), ("trb", "Rebounds"), ("ast", "Assists"),
                        ("stl", "Steals"), ("blk", "Blocks"), ("tpm", "3PT made")):
        cells = [f"{player_label(league, pid)} {v:.1f}" for pid, v in leaders(league, totals, stat)]
        print(f"  {label:<10}" + " | ".join(cells))


def cmd_season(args) -> None:
    league = make_league(args)
    set_engine(league, args)
    summary = play_full_season(league, random.Random(args.seed))
    if args.out:
        save_league(league, args.out)
        print(f"Saved to {args.out}\n")
    print_standings(league)
    print("Playoffs")
    print_playoffs(league)
    champ = league.teams[summary["champion"]]
    print(f"\nChampions: {champ.full_name} (won the finals {summary['finals']})\n")
    print("Awards")
    print_awards(league, summary)
    print()
    print_leaders(league, summary["season"])
    for line in summary["deadline_trades"]:
        print(line)


def cmd_career(args) -> None:
    """Simulate many seasons and show the league's history."""
    if args.load:
        league = load_league(args.load)
    else:
        league = make_league(args)
    set_engine(league, args)
    rng = random.Random(args.seed)
    print(f"{'Season':<9}{'Champion':<26}{'Finals':<8}{'Best':>5}{'Worst':>6}  MVP / Rookie of the Year")
    first_draft = None
    for _ in range(args.seasons):
        s = play_full_season(league, rng)
        wins = sorted((w for _, w, _ in s["standings"]), reverse=True)
        names = s["award_names"]
        print(f"{s['season']}-{(s['season'] + 1) % 100:02d}  {league.teams[s['champion']].full_name:<26}"
              f"{s['finals']:<8}{wins[0]:>5}{wins[-1]:>6}  {names['mvp']} / {names['roy'] or '-'}")
        report = run_offseason(league, rng)
        if first_draft is None and report["draft"]:
            first_draft = next(p for p in league.players.values()
                               if p.draft_year == league.season and p.draft_pick == 1)
        if report["problems"]:
            print("  problems:", report["problems"])
    if args.out:
        save_league(league, args.out)
        print(f"\nSaved to {args.out}")

    print("\nTitles")
    titles: dict[int, int] = {}
    for h in league.history:
        titles[h["champion"]] = titles.get(h["champion"], 0) + 1
    for tid, n in sorted(titles.items(), key=lambda kv: -kv[1]):
        print(f"  {league.teams[tid].full_name:<26}{n}")

    print("\nCareer points leaders (regular season)")
    career_pts = sorted(((sum(c["pts"] for c in p.career if not c["playoffs"]), p)
                         for p in league.players.values() if p.career), key=lambda x: -x[0])[:5]
    for pts, p in career_pts:
        seasons = [c for c in p.career if not c["playoffs"]]
        gp = sum(c["gp"] for c in seasons)
        status = "retired" if p.retired else league.teams[p.team_id].abbreviation if p.team_id else "FA"
        print(f"  {p.name:<22}{pts:>6} pts in {gp} games over {len(seasons)} seasons ({status})")

    if first_draft:
        p = first_draft
        print(f"\nFollowing {p.name}, #1 pick in {p.draft_year} (potential {p.potential_ability}):")
        for c in (c for c in p.career if not c["playoffs"]):
            pg = career_per_game(c)
            print(f"  {c['season']} age {c['age']} {c['team']}  CA {c['ca']:>3}  "
                  f"{pg['mpg']:4.1f} mpg {pg['ppg']:4.1f} ppg {pg['rpg']:4.1f} rpg {pg['apg']:4.1f} apg")


def cmd_watch(args) -> None:
    import webbrowser
    from pathlib import Path

    from .spatial import HalfCourtGame
    from .spatial.export import write_html

    league = load_league(args.load) if args.load else generate_league(seed=args.seed)
    by_abbr = {t.abbreviation.lower(): t.id for t in league.teams.values()}
    ids = sorted(league.teams)
    home, away = ids[0], ids[1]
    if args.load and league.user_team_id is not None:
        home = league.user_team_id
        upcoming = sorted((g for g in league.games.values() if not g.played
                           and home in (g.home_team_id, g.away_team_id)), key=lambda g: g.day)
        if upcoming:
            g = upcoming[0]
            away = g.away_team_id if g.home_team_id == home else g.home_team_id
        else:
            away = next(t for t in ids if t != home)
    if args.home:
        home = by_abbr[args.home.lower()]
    if args.away:
        away = by_abbr[args.away.lower()]
    if home == away:
        raise SystemExit("pick two different teams")
    h, a = league.teams[home], league.teams[away]
    if getattr(args, "full", False) or getattr(args, "quarter", None):
        from .spatial.fullgame import FullGame
        full = FullGame(league, home, away, random.Random(args.seed), record=True)
        sim = full.run()
        quarter = getattr(args, "quarter", None)
        if quarter is not None and not 1 <= quarter <= full.period:
            raise SystemExit(f"the game had {full.period} periods")
        path = write_html(full.to_dict(period=quarter), args.out)
        r = sim.result
        ot = f" ({r.overtimes} OT)" if r.overtimes else ""
        part = f"Q{quarter}" if quarter and quarter <= 4 else f"OT{quarter - 4}" if quarter else "full game"
        print(f"{a.abbreviation} {r.away_score} @ {h.abbreviation} {r.home_score}{ot}; "
              f"{part} -> {path.resolve()} ({path.stat().st_size / 1e6:.1f} MB)")
        if not args.no_open:
            webbrowser.open(Path(path).resolve().as_uri())
        return
    game = HalfCourtGame(league, home, away, random.Random(args.seed))
    game.play(args.possessions)
    path = write_html(game.to_dict(), args.out)
    print(f"{a.abbreviation} {game.score['away']} @ {h.abbreviation} {game.score['home']} "
          f"after {args.possessions} half-court possessions -> {path.resolve()}")
    if not args.no_open:
        webbrowser.open(Path(path).resolve().as_uri())


def main() -> None:
    ap = argparse.ArgumentParser(prog="hoopsim")
    sub = ap.add_subparsers(dest="cmd")

    new = sub.add_parser("new", help="generate and save a league")
    new.add_argument("--engine", choices=["statistical", "spatial", "hybrid"], help="match engine: statistical (fast, default), spatial (players on a court, ~0.7 s a game) or hybrid (spatial for your team's games only)")
    new.add_argument("--seed", type=int, default=0)
    new.add_argument("--teams", type=int, default=12)
    new.add_argument("--league", help="start from a league file (JSON) instead of generating one")
    new.add_argument("--out", default="league.db")
    new.add_argument("--team", help="team abbreviation to show (default: first team)")

    game = sub.add_parser("game", help="simulate one game and print the box score")
    game.add_argument("--engine", choices=["statistical", "spatial", "hybrid"], help="match engine: statistical (fast, default), spatial (players on a court, ~0.7 s a game) or hybrid (spatial for your team's games only)")
    game.add_argument("--seed", type=int, default=0)
    game.add_argument("--pbp", action="store_true", help="print the play-by-play")

    season = sub.add_parser("season", help="simulate a full season with playoffs and awards")
    season.add_argument("--engine", choices=["statistical", "spatial", "hybrid"], help="match engine: statistical (fast, default), spatial (players on a court, ~0.7 s a game) or hybrid (spatial for your team's games only)")
    season.add_argument("--seed", type=int, default=0)
    season.add_argument("--teams", type=int, default=12)
    season.add_argument("--league", help="start from a league file (JSON) instead of generating one")
    season.add_argument("--out", help="save the finished season to this file")

    career = sub.add_parser("career", help="simulate many seasons (draft, free agency, ageing...)")
    career.add_argument("--engine", choices=["statistical", "spatial", "hybrid"], help="match engine: statistical (fast, default), spatial (players on a court, ~0.7 s a game) or hybrid (spatial for your team's games only)")
    career.add_argument("--seasons", type=int, default=10)
    career.add_argument("--seed", type=int, default=0)
    career.add_argument("--teams", type=int, default=12)
    career.add_argument("--league", help="start from a league file (JSON) instead of generating one")
    career.add_argument("--load", help="continue a saved league instead of generating one")
    career.add_argument("--out", help="save the league afterwards")

    play = sub.add_parser("play", help="be the general manager of one team")
    play.add_argument("--engine", choices=["statistical", "spatial", "hybrid"], help="match engine: statistical (fast, default), spatial (players on a court, ~0.7 s a game) or hybrid (spatial for your team's games only)")
    play.add_argument("--team", help="team abbreviation (otherwise you'll be asked)")
    play.add_argument("--seed", type=int, default=None)
    play.add_argument("--teams", type=int, default=12)
    play.add_argument("--league", help="start from a league file (JSON) instead of generating one")
    play.add_argument("--load", help="continue a saved game")
    play.add_argument("--out", help="file to save to (default hoopsim_save.db)")

    watch = sub.add_parser("watch", help="watch the spatial prototype in your browser")
    watch.add_argument("--seed", type=int, default=0)
    watch.add_argument("--possessions", type=int, default=40)
    watch.add_argument("--home", help="home team abbreviation (default: first team)")
    watch.add_argument("--away", help="away team abbreviation (default: second team)")
    watch.add_argument("--load", help="use a saved league (your team at home vs its next opponent)")
    watch.add_argument("--out", default="hoopsim_watch.html")
    watch.add_argument("--no-open", action="store_true", help="don't open the browser")
    watch.add_argument("--full", action="store_true",
                       help="a full game: four quarters, transition, substitutions, fatigue, fouls")
    watch.add_argument("--quarter", type=int, help="with --full: export just this period (5 = first overtime)")

    web = sub.add_parser("web", help="play as general manager in your browser")
    web.add_argument("--load", help="open a saved game")
    web.add_argument("--save", help="default file to save to (default: the loaded file or hoopsim_save.db)")
    web.add_argument("--leagues", help="folder of league files offered under New game (default: ./leagues)")
    web.add_argument("--port", type=int, default=8000)
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--no-open", action="store_true", help="don't open the browser")

    args = ap.parse_args()
    if args.cmd == "web":
        try:
            from .web.app import serve
        except ImportError as e:
            raise SystemExit(f"the web interface needs extra packages ({e.name} is missing): "
                             "pip install -r requirements-web.txt")
        serve(load=args.load, port=args.port, host=args.host, open_browser=not args.no_open, save=args.save, leagues=args.leagues)
        return
    if args.cmd == "watch":
        cmd_watch(args)
        return
    if args.cmd == "play":
        from .play import start
        start(args)
        return
    if args.cmd is None:  # bare `python -m hoopsim` behaves like `new`
        args = ap.parse_args(["new"])
    {"new": cmd_new, "game": cmd_game, "season": cmd_season, "career": cmd_career}[args.cmd](args)


if __name__ == "__main__":
    main()
