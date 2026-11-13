"""Play as the general manager of one team, in the terminal.

    python -m hoopsim play                 # new league, pick a team
    python -m hoopsim play --team HCM      # new league as that team
    python -m hoopsim play --load my.db    # continue a saved game

Type ``help`` for commands. During the offseason you're asked for each
decision; answer ``auto`` to hand any of them to your assistant.
"""
from __future__ import annotations

import random
import re
import shlex
from typing import Callable, Optional

from .db import load_league, save_league
from .generate import generate_league
from .manager import (
    AIManager, choose_team, describe_tactics, describe_tendencies, free_agent_ask, hidden_traits,
    offer_contract, propose_trade, release_player, scouted_potential, set_auto_lineup, set_minutes,
    set_player_tendencies, set_starters, set_tactics,
)
from .contracts import cap_room, payment_route, payroll_for
from .lineup import POSITIONS
from .models import Attributes, League, Phase, Player
from .playoffs import play_playoffs, ranked, start_playoffs, team_records
from .season import (
    finish_season, play_regular_season, regular_season_over, run_offseason, trade_deadline_day,
)
from .stats import career_per_game, leaders, per_game, player_label, season_totals


class IO:
    """Input/output, swappable for tests."""

    def __init__(self, ask: Callable[[str], str] = input, say: Callable[[str], None] = print):
        self.ask, self.say = ask, say


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------
def money(n: float) -> str:
    return f"${n / 1e6:.1f}M"


def height(cm: int) -> str:
    inches = round(cm / 2.54)
    return f"{inches // 12}'{inches % 12}"


def morale_word(m: float) -> str:
    return ("unhappy" if m < 30 else "frustrated" if m < 45 else "content" if m < 60
            else "happy" if m < 75 else "delighted")


def potential_text(league: League, p: Player) -> str:
    """Scouts' estimate of the ceiling. Past 29 nobody grows, so it's moot."""
    if p.age(league.current_date) >= 29:
        return "peaked"
    est, lo, hi = scouted_potential(league, p)
    return f"{est} ({lo}-{hi})"


def parse_money(text: str) -> int:
    """'12.5' or '12.5m' -> 12_500_000; '900k' -> 900_000."""
    t = text.lower().strip().lstrip("$")
    if t.endswith("k"):
        return int(float(t[:-1]) * 1e3)
    return int(round(float(t.rstrip("m")) * 1e6))


def parse_ids(text: str) -> list[int]:
    return [int(x) for x in text.replace(" ", "").split(",") if x]


# --------------------------------------------------------------------------
# Decisions during the offseason
# --------------------------------------------------------------------------
class InteractiveManager(AIManager):
    """Asks you for every decision; 'auto' hands one (or a whole phase) to the AI."""

    def __init__(self, io: IO):
        self.io = io
        self.autopilot: set[str] = set()

    def _ask(self, prompt: str) -> str:
        return self.io.ask(prompt).strip()

    def exercise_team_option(self, league, player, fair_salary):
        if "options" in self.autopilot:
            return super().exercise_team_option(league, player, fair_salary)
        io = self.io
        io.say(f"\nTeam option: {player.name} ({player.position.value}, {player.age(league.current_date)}, "
               f"CA {player.current_ability}) is owed {money(player.contract.salary)} next season. "
               f"Market value about {money(fair_salary)}.")
        while True:
            a = self._ask("Exercise the option? [y/n/auto] ").lower()
            if a in ("y", "yes"):
                return True
            if a in ("n", "no"):
                return False
            if a == "auto":
                self.autopilot.add("options")
                return super().exercise_team_option(league, player, fair_salary)

    def resign(self, league, expiring):
        if "resign" in self.autopilot:
            return super().resign(league, expiring)
        io = self.io
        io.say("\nRE-SIGNING: these contracts have ended. Make offers or let them walk.")
        io.say(f"  Payroll next season: {money(payroll_for(league, league.user_team_id, league.season))}, "
               f"cap {money(league.rules.salary_cap)}, tax line {money(league.rules.luxury_tax_line)}")
        io.say(f"  {'ID':>5} {'Name':<20} Pos Age  CA  Potential   Wants")
        for p, ask, years in expiring:
            io.say(f"  {p.id:>5} {p.name:<20} {p.position.value:<3} {p.age(league.current_date):>3} "
                   f"{p.current_ability:>3}  {potential_text(league, p):<11} {money(ask)} x {years}y"
                   f"  ({morale_word(p.morale)})")
        io.say("  Offer as 'ID SALARY YEARS' (salary in $M), several separated by ';'.\n"
               "  Paying over the ask makes him likelier to stay. Enter = let all walk, 'auto' = assistant decides.")
        ids = {p.id for p, _, _ in expiring}
        while True:
            a = self._ask("Offers: ")
            if a.lower() == "auto":
                self.autopilot.add("resign")
                return super().resign(league, expiring)
            try:
                offers = {}
                for part in filter(None, (x.strip() for x in a.split(";"))):
                    pid, sal, yrs = part.split()
                    if int(pid) not in ids:
                        raise ValueError(f"{pid} isn't on the list")
                    offers[int(pid)] = (parse_money(sal), int(yrs))
                return offers
            except ValueError as e:
                io.say(f"  Couldn't read that ({e}). Example: 1234 12.5 3; 1240 2 1")

    def draft_pick(self, league, pick, available):
        if "draft" in self.autopilot:
            return super().draft_pick(league, pick, available)
        io = self.io
        board = sorted(available, key=lambda p: -(0.35 * p.current_ability + 0.65 * scouted_potential(league, p)[0]))
        io.say(f"\nDRAFT: you're on the clock with pick #{pick}. Your scouts' board:")
        io.say(f"  {'ID':>5} {'Name':<20} Pos Age Height  CA  Potential   Notes")
        for p in board[:12]:
            io.say(f"  {p.id:>5} {p.name:<20} {p.position.value:<3} {p.age(league.current_date):>3} "
                   f"{height(p.physique.height_cm):<6} {p.current_ability:>3}  {potential_text(league, p):<11} "
                   f"{', '.join(hidden_traits(p)[:3])}")
        by_id = {p.id: p for p in available}
        while True:
            a = self._ask("Pick (ID, or 'auto'): ").lower()
            if a == "auto":
                self.autopilot.add("draft")
                return super().draft_pick(league, pick, available)
            if a.isdigit() and int(a) in by_id:
                return by_id[int(a)]
            io.say("  Not on the board.")

    def free_agency(self, league, pool, terms, round_no):
        if "fa" in self.autopilot:
            return super().free_agency(league, pool, terms, round_no)
        io = self.io
        tid = league.user_team_id
        room = cap_room(league, tid, league.season)
        mle = tid not in league.mle_used
        io.say(f"\nFREE AGENCY round {round_no}. Roster {len(league.roster(tid))}/{league.rules.max_roster}, "
               f"cap room {money(max(0, room))}"
               + (f", mid-level exception available (up to {money(league.rules.mid_level_exception)})" if mle else ""))
        io.say(f"  {'ID':>5} {'Name':<20} Pos Age  CA  Potential   Asking")
        for p in pool[:15]:
            years, ask = terms[p.id]
            io.say(f"  {p.id:>5} {p.name:<20} {p.position.value:<3} {p.age(league.current_date):>3} "
                   f"{p.current_ability:>3}  {potential_text(league, p):<11} {money(ask)} x {years}y")
        io.say("  Offer as 'ID SALARY YEARS'; several with ';'. Enter = pass this round,\n"
               "  'done' = stop bidding this summer, 'auto' = assistant handles free agency.")
        while True:
            a = self._ask("Offers: ")
            if a.lower() == "auto":
                self.autopilot.add("fa")
                return super().free_agency(league, pool, terms, round_no)
            if a.lower() == "done":
                self.autopilot.add("fa_done")
                return []
            try:
                offers = []
                for part in filter(None, (x.strip() for x in a.split(";"))):
                    pid, sal, yrs = part.split()
                    salary = parse_money(sal)
                    if int(pid) not in terms:
                        raise ValueError(f"{pid} isn't a free agent")
                    if not payment_route(league, tid, salary, league.season, mle):
                        raise ValueError(f"you can't pay {money(salary)} under the cap rules")
                    offers.append((int(pid), salary, int(yrs)))
                return offers
            except ValueError as e:
                io.say(f"  Couldn't read that ({e}). Example: 1234 8 2")

    def respond_to_trade(self, league, offer):
        if "trades" in self.autopilot:
            return super().respond_to_trade(league, offer)
        io = self.io
        team = league.teams[offer["team_id"]]

        def describe(ids):
            return "; ".join(f"{league.players[i].name} ({league.players[i].position.value}, "
                             f"{league.players[i].age(league.current_date)}, CA {league.players[i].current_ability}, "
                             f"{money(league.players[i].contract.salary)} to {league.players[i].contract.expires_season})"
                             for i in ids)
        io.say(f"\nTRADE OFFER from the {team.full_name}:\n  You give: {describe(offer['you_give'])}\n"
               f"  You get:  {describe(offer['you_get'])}")
        while True:
            a = self._ask("Accept? [y/n/auto] ").lower()
            if a in ("y", "yes"):
                return True
            if a in ("n", "no"):
                return False
            if a == "auto":
                self.autopilot.add("trades")
                return super().respond_to_trade(league, offer)

    def choose_waivers(self, league, count):
        io = self.io
        io.say(f"\nROSTER CUTS: you have {count} too many players. Choose who to waive.")
        roster = league.roster(league.user_team_id)
        for p in sorted(roster, key=lambda q: q.current_ability):
            io.say(f"  {p.id:>5} {p.name:<20} {p.position.value:<3} {p.age(league.current_date):>3} "
                   f"CA {p.current_ability:>3}  {money(p.contract.salary)}")
        ids = {p.id for p in roster}
        while True:
            a = self._ask(f"Waive {count} (IDs separated by commas, or 'auto'): ").lower()
            if a == "auto":
                return super().choose_waivers(league, count)
            try:
                chosen = parse_ids(a)
                if len(set(chosen)) == count and set(chosen) <= ids:
                    return chosen
            except ValueError:
                pass
            io.say(f"  Pick exactly {count} players from your roster.")


# --------------------------------------------------------------------------
# The game
# --------------------------------------------------------------------------
HELP = """\
Season
  sim [N]            play the next N days (default 1)     week     play 7 days
  deadline           play to the trade deadline           end      play the rest of the regular season
  next               regular season over: play the playoffs, then the offseason
Your team
  roster [ABBR]      roster with ratings and contracts    player ID    full scouting report
  lineup             starters and minutes                 auto on|off  assistant sets the lineup?
  start A B C D E    set starters (PG SG SF PF C ids)     minutes ID=MIN ...   set minute targets
  tactics [k=v ...]  system=pace_space|seven_seconds|motion|triangle|grit_grind|inside_out|heliocentric|balanced
                     defense_system=man|switch|drop|zone|press  options=ID,ID,ID (1st,2nd,3rd scoring options; options= clears)
                     pace=1-20 three=1-20 crash=1-20 depth=5-15 focus=inside|balanced|perimeter
                     defense=man|switch|zone|press
  instruct ID k=v    player instructions: three mid drive post pass cut (1-20, 10 = normal)
  finances           payroll, cap room, contracts
Moves
  fa [N]             best free agents and what they want  sign ID SALARY YEARS (salary in $M)
  release ID         waive a player                       trade 12,15 for ABBR 40,41
League
  standings          leaders     schedule     results     teams     history
  save [FILE]        quit
"""


class Game:
    def __init__(self, league: League, io: IO, rng: random.Random, save_path: str = "hoopsim_save.db"):
        self.league, self.io, self.rng = league, io, rng
        self.manager = InteractiveManager(io)
        self.save_path = save_path

    # -- helpers ---------------------------------------------------------------
    @property
    def me(self):
        return self.league.teams[self.league.user_team_id]

    def say(self, text: str = "") -> None:
        self.io.say(text)

    def team_by_abbr(self, abbr: str):
        for t in self.league.teams.values():
            if t.abbreviation.lower() == abbr.lower():
                return t
        raise ValueError(f"no team '{abbr}' (see 'teams')")

    def record(self, team_id: int) -> str:
        w, l, _ = team_records(self.league)[team_id]
        return f"{w}-{l}"

    def status_line(self) -> str:
        lg = self.league
        rank = ranked(lg, [t.id for t in lg.teams.values() if t.conference == self.me.conference])
        place = rank.index(self.me.id) + 1
        extra = ""
        deadline = trade_deadline_day(lg)
        played_days = {g.day for g in lg.games.values() if g.season == lg.season and g.played}
        if deadline and max(played_days, default=0) < deadline:
            extra = f" | trade deadline in {deadline - max(played_days, default=0)} days"
        if regular_season_over(lg):
            extra = " | regular season over: type 'next' for the playoffs"
        return (f"[{lg.current_date:%b %d %Y}] {self.me.full_name} {self.record(self.me.id)} "
                f"({place} in the {self.me.conference}){extra}")

    # -- main loop ---------------------------------------------------------------
    def run(self) -> None:
        self.say(f"\nYou are the general manager of the {self.me.full_name}. Type 'help' for commands.\n")
        self.cmd_roster([])
        while True:
            self.say("")
            self.say(self.status_line())
            try:
                line = self.io.ask("> ")
            except EOFError:
                line = "quit"
            try:
                words = shlex.split(line)
            except ValueError:
                words = line.split()
            if not words:
                continue
            cmd, args = words[0].lower(), words[1:]
            if cmd in ("quit", "exit", "q"):
                if self.io.ask("Save before quitting? [y/n] ").strip().lower().startswith("y"):
                    self.cmd_save([])
                self.say("Goodbye!")
                return
            handler = getattr(self, f"cmd_{cmd}", None)
            if handler is None:
                self.say(f"Unknown command '{cmd}'. Type 'help'.")
                continue
            try:
                handler(args)
            except (ValueError, KeyError, IndexError) as e:
                self.say(f"Couldn't do that: {e}")

    # -- simulation -----------------------------------------------------------------
    def _sim_days(self, days: Optional[int]) -> None:
        lg = self.league
        if regular_season_over(lg):
            self.say("The regular season is over. Type 'next' for the playoffs.")
            return
        before = {g.id for g in lg.games.values() if g.played}
        hurt_before = {p.id for p in lg.roster(self.me.id) if p.injury}
        events = play_regular_season(lg, self.rng, days=days, manager=self.manager)
        new = sorted((g for g in lg.games.values() if g.played and g.id not in before
                      and self.me.id in (g.home_team_id, g.away_team_id)), key=lambda g: g.day)
        for g in new[-10:]:
            self.say("  " + self.result_line(g))
        if len(new) > 10:
            self.say(f"  ... and {len(new) - 10} earlier games")
        for p in lg.roster(self.me.id):
            if p.injury and p.id not in hurt_before:
                self.say(f"  INJURY: {p.name} - {p.injury.description}, out {p.injury.games_remaining} games")
        for e in events:
            self.say("  " + e)

    def result_line(self, g) -> str:
        lg = self.league
        home = g.home_team_id == self.me.id
        opp = lg.teams[g.away_team_id if home else g.home_team_id]
        mine, theirs = ((g.result.home_score, g.result.away_score) if home
                        else (g.result.away_score, g.result.home_score))
        wl = "W" if mine > theirs else "L"
        ot = f" ({g.result.overtimes}OT)" if g.result.overtimes else ""
        best = ""
        if g.box_score:
            box = g.box_score.home if home else g.box_score.away
            top = max(box.lines, key=lambda l: l.pts)
            best = f"  - {lg.players[top.player_id].name} {top.pts} pts, {top.trb} reb, {top.ast} ast"
        return f"{wl} {mine}-{theirs} {'vs' if home else '@'} {opp.abbreviation}{ot}{best}"

    def cmd_sim(self, args):
        self._sim_days(int(args[0]) if args else 1)

    def cmd_week(self, args):
        self._sim_days(7)

    def cmd_end(self, args):
        self._sim_days(None)

    def cmd_deadline(self, args):
        lg = self.league
        deadline = trade_deadline_day(lg)
        played = max((g.day for g in lg.games.values() if g.season == lg.season and g.played), default=0)
        if deadline is None or played >= deadline:
            self.say("The trade deadline has passed.")
            return
        self._sim_days(deadline - played)

    def cmd_next(self, args):
        lg = self.league
        if not regular_season_over(lg):
            self.say("The regular season isn't over yet ('end' plays the rest).")
            return
        start_playoffs(lg)
        made_it = any(self.me.id in (s.high_id, s.low_id) for s in lg.playoffs)
        self.say("\nPLAYOFFS" + ("" if made_it else " (you missed out this year)"))
        champ = play_playoffs(lg, self.rng)
        for s in sorted(lg.playoffs, key=lambda s: (s.round, s.id)):
            stage = "Finals" if s.conference is None else f"{s.conference} round {s.round}"
            mark = " <-- you" if self.me.id in (s.high_id, s.low_id) else ""
            self.say(f"  {stage:<16} ({s.high_seed}) {lg.teams[s.high_id].abbreviation} {s.high_wins}-{s.low_wins} "
                     f"{lg.teams[s.low_id].abbreviation} ({s.low_seed}){mark}")
        summary = finish_season(lg)
        self.say(f"\n{lg.teams[champ].full_name.upper()} WIN THE TITLE ({summary['finals']})"
                 + ("  -- CONGRATULATIONS!" if champ == self.me.id else ""))
        for key, label in (("mvp", "MVP"), ("finals_mvp", "Finals MVP"), ("dpoy", "Defensive POY"),
                           ("roy", "Rookie of the Year"), ("sixth_man", "Sixth Man"), ("mip", "Most Improved")):
            self.say(f"  {label:<20}{player_label(lg, summary['awards'].get(key))}")
        self.say("\nOFFSEASON. Your decisions follow; answer 'auto' to hand any of them to your assistant.")
        self.manager.autopilot -= {"fa_done"}
        report = run_offseason(lg, self.rng, self.manager)
        self.offseason_summary(report)
        self.say(f"\nWelcome to the {lg.season}-{(lg.season + 1) % 100:02d} season!")
        self.cmd_roster([])

    def offseason_summary(self, report: dict) -> None:
        abbr = self.me.abbreviation
        about_me = re.compile(rf"\b{re.escape(abbr)}\b|\byou(r)?\b", re.IGNORECASE)
        mine = [l for k in ("options", "resigned", "draft", "free_agency", "trades", "waived")
                for l in report.get(k, []) if about_me.search(l)]
        dev = report["development"]
        roster = self.league.roster(self.me.id)
        self.say("\nSummer report")
        for line in mine:
            self.say("  " + line)
        movers = sorted((p for p in roster if p.id in dev), key=lambda p: -abs(dev[p.id]))[:6]
        if movers:
            self.say("  Development: " + ", ".join(f"{p.name} {dev[p.id]:+d}" for p in movers))
        for n in report.get("notes", []):
            self.say("  " + n)
        retired = report.get("retired", [])
        if retired:
            self.say(f"  Around the league, {len(retired)} players retired, including " + "; ".join(retired[:3]))

    # -- your team ----------------------------------------------------------------------
    def cmd_roster(self, args):
        lg = self.league
        team = self.team_by_abbr(args[0]) if args else self.me
        totals = season_totals(lg)
        self.say(f"\n{team.full_name} ({self.record(team.id)}), payroll {money(lg.payroll(team.id))}")
        self.say(f"  {'ID':>5} {'#':>2} {'Name':<20} Pos Age Ht    CA  Potential    3PT FIN PAS DEF REB  "
                 f"{'Salary':>7} Thru  MPG  PPG  Status")
        starters = set(team.tactics.starters)
        for p in lg.roster(team.id):
            a, t = p.attributes, totals.get(p.id)
            status = (f"OUT {p.injury.games_remaining}g ({p.injury.description})" if p.injury
                      else morale_word(p.morale))
            self.say(f"{'*' if p.id in starters else ' '} {p.id:>5} {p.jersey_number or 0:>2} {p.name:<20} "
                     f"{p.position.value:<3} {p.age(lg.current_date):>3} {height(p.physique.height_cm):<5} "
                     f"{p.current_ability:>3}  {potential_text(lg, p):<12} {a.three_point:>3} {a.finishing:>3} "
                     f"{a.passing:>3} {max(a.perimeter_defense, a.interior_defense):>3} "
                     f"{a.defensive_rebounding:>3}  {money(p.contract.salary):>7} {p.contract.expires_season} "
                     f"{per_game(t, 'min') if t else 0:4.1f} {per_game(t, 'pts') if t else 0:4.1f}  {status}")
        self.say("  * = starter. Potential is your scouts' estimate (range).")

    def cmd_player(self, args):
        lg = self.league
        p = lg.players[int(args[0])]
        team = lg.teams[p.team_id].full_name if p.team_id else ("retired" if p.retired else "free agent")
        self.say(f"\n{p.name}  #{p.jersey_number}  {p.position.value}"
                 + (f"/{'/'.join(x.value for x in p.secondary_positions)}" if p.secondary_positions else "")
                 + f"  {team}")
        self.say(f"  Age {p.age(lg.current_date)}, {height(p.physique.height_cm)} ({p.physique.height_cm} cm), "
                 f"{p.physique.weight_kg} kg, wingspan {p.physique.wingspan_cm} cm, {p.nationality}")
        draft = ("" if p.draft_year is None else
                 f" #{p.draft_pick} pick in {p.draft_year}." if p.draft_pick else f" Undrafted in {p.draft_year}.")
        self.say(f"  Current ability {p.current_ability}, potential {potential_text(lg, p)}.{draft}")
        groups = [("Scoring", ["finishing", "close_range", "mid_range", "three_point", "free_throw", "drawing_fouls"]),
                  ("Playmaking", ["passing", "ball_handling", "vision"]),
                  ("Defence", ["perimeter_defense", "interior_defense", "stealing", "blocking"]),
                  ("Rebounding", ["offensive_rebounding", "defensive_rebounding"]),
                  ("Physical", ["speed", "strength", "vertical", "stamina"]),
                  ("Mental", ["basketball_iq", "composure"])]
        for label, names in groups:
            self.say(f"  {label:<11}" + "  ".join(f"{n.replace('_', ' ')} {getattr(p.attributes, n)}" for n in names))
        traits = hidden_traits(p)
        self.say(f"  Scouting notes: {', '.join(traits) if traits else 'nothing stands out'}")
        self.say(f"  Plays: {describe_tendencies(p)}")
        if p.contract:
            opt = f", {p.contract.option.value} option" if p.contract.option else ""
            self.say(f"  Contract: {money(p.contract.salary)} through {p.contract.expires_season}{opt}")
        self.say(f"  Morale: {morale_word(p.morale)}"
                 + (f"   Injury: {p.injury.description}, out {p.injury.games_remaining} games" if p.injury else ""))
        t = season_totals(lg).get(p.id)
        if t:
            self.say(f"  This season: {t['gp']:.0f} gp, {per_game(t, 'min'):.1f} min, {per_game(t, 'pts'):.1f} pts, "
                     f"{per_game(t, 'trb'):.1f} reb, {per_game(t, 'ast'):.1f} ast, "
                     f"{t['fgm'] / max(1, t['fga']):.3f} FG%, {t['tpm'] / max(1, t['tpa']):.3f} 3P%")
        career = [c for c in p.career if not c["playoffs"]]
        if career:
            self.say("  Career:")
            for c in career[-8:]:
                pg = career_per_game(c)
                self.say(f"    {c['season']} {c['team']:<4} age {c.get('age', '?'):>2} CA {c.get('ca', '?'):>3}  "
                         f"{c['gp']:>2} gp {pg['mpg']:4.1f} mpg {pg['ppg']:4.1f} ppg {pg['rpg']:4.1f} rpg {pg['apg']:4.1f} apg")

    def cmd_lineup(self, args):
        lg, t = self.league, self.me.tactics
        self.say(f"\nLineup ({'set by your assistant' if self.me.auto_lineup else 'set by you'})")
        for pos, pid in zip(POSITIONS, t.starters):
            p = lg.players[pid]
            self.say(f"  {pos.value}  {p.id:>5} {p.name:<20} CA {p.current_ability:>3}  "
                     f"(at {pos.value}: {p.ability_at(pos)})  {t.minutes_targets.get(pid, 0)} min")
        bench = sorted(((pid, m) for pid, m in t.minutes_targets.items() if pid not in t.starters),
                       key=lambda kv: -kv[1])
        for pid, m in bench:
            p = lg.players[pid]
            self.say(f"  bench {p.id:>5} {p.name:<20} CA {p.current_ability:>3}  {m} min")
        self.say("  Tactics: " + describe_tactics(lg))

    def cmd_start(self, args):
        ok, msg = set_starters(self.league, [int(a) for a in args])
        self.say(msg)

    def cmd_minutes(self, args):
        changes = {}
        for a in args:
            pid, m = a.split("=")
            changes[int(pid)] = int(m)
        ok, msg = set_minutes(self.league, changes)
        self.say(msg)

    def cmd_auto(self, args):
        if not args or args[0].lower() not in ("on", "off"):
            self.say("Use 'auto on' or 'auto off'.")
            return
        self.say(set_auto_lineup(self.league, args[0].lower() == "on"))

    def cmd_tactics(self, args):
        if not args:
            self.say("Tactics: " + describe_tactics(self.league))
            return
        changes = dict(a.split("=", 1) for a in args)
        ok, msg = set_tactics(self.league, **changes)
        self.say(msg)

    def cmd_instruct(self, args):
        if len(args) < 2:
            self.say("Usage: instruct ID three=16 mid=12 drive=8 post=4 pass=14 cut=10   (1-20, 10 = normal)")
            return
        changes = dict(a.split("=", 1) for a in args[1:])
        ok, msg = set_player_tendencies(self.league, int(args[0]), **changes)
        self.say(msg)

    def cmd_finances(self, args):
        lg, tid = self.league, self.me.id
        r = lg.rules
        pay = payroll_for(lg, tid, lg.season)
        self.say(f"\nPayroll {money(pay)} | cap {money(r.salary_cap)} | tax line {money(r.luxury_tax_line)} | "
                 f"cap room {money(max(0, cap_room(lg, tid, lg.season)))}")
        self.say(f"Mid-level exception: {'available' if tid not in lg.mle_used else 'used'} "
                 f"(up to {money(r.mid_level_exception)}); minimum contract {money(r.min_salary)}")
        for year in range(lg.season, lg.season + 4):
            self.say(f"  committed for {year}: {money(payroll_for(lg, tid, year))}")
        for p in sorted(lg.roster(tid), key=lambda q: -q.contract.salary):
            opt = f" ({p.contract.option.value} option)" if p.contract.option else ""
            self.say(f"  {p.name:<22}{money(p.contract.salary):>8} through {p.contract.expires_season}{opt}")

    # -- moves --------------------------------------------------------------------------------
    def cmd_fa(self, args):
        lg = self.league
        n = int(args[0]) if args else 12
        self.say(f"\nFree agents (roster {len(lg.roster(self.me.id))}/{lg.rules.max_roster}, "
                 f"cap room {money(max(0, cap_room(lg, self.me.id, lg.season)))}"
                 + (", MLE available)" if self.me.id not in lg.mle_used else ")"))
        self.say(f"  {'ID':>5} {'Name':<20} Pos Age  CA  Potential    Wants")
        for p in lg.free_agents()[:n]:
            years, ask = free_agent_ask(lg, p)
            inj = f"  (injured, {p.injury.games_remaining}g)" if p.injury else ""
            self.say(f"  {p.id:>5} {p.name:<20} {p.position.value:<3} {p.age(lg.current_date):>3} "
                     f"{p.current_ability:>3}  {potential_text(lg, p):<12} {money(ask)} x {years}y{inj}")

    def cmd_sign(self, args):
        if len(args) != 3:
            self.say("Usage: sign ID SALARY YEARS   e.g. sign 1234 1.5 1")
            return
        ok, msg = offer_contract(self.league, int(args[0]), parse_money(args[1]), int(args[2]), self.rng)
        self.say(msg)

    def cmd_release(self, args):
        p = self.league.players[int(args[0])]
        if self.io.ask(f"Release {p.name}? [y/n] ").strip().lower().startswith("y"):
            ok, msg = release_player(self.league, p.id)
            self.say(msg)

    def cmd_trade(self, args):
        text = " ".join(args)
        if " for " not in f" {text} ":
            self.say("Usage: trade 12,15 for ABBR 40,41   (your ids, then theirs; use '-' for nothing)")
            return
        give_text, rest = text.split("for", 1)
        rest = rest.split()
        team = self.team_by_abbr(rest[0])
        give = [] if give_text.strip() in ("", "-") else parse_ids(give_text)
        get = [] if len(rest) < 2 or rest[1] == "-" else parse_ids("".join(rest[1:]))
        lg = self.league
        self.say(f"Proposing: {', '.join(lg.players[i].name for i in give) or 'nothing'} for "
                 f"{', '.join(lg.players[i].name for i in get) or 'nothing'} ({team.abbreviation})")
        ok, msg = propose_trade(lg, give, team.id, get)
        self.say(msg)

    # -- league ---------------------------------------------------------------------------------
    def cmd_standings(self, args):
        lg = self.league
        rec = team_records(lg)
        for conf in sorted({t.conference for t in lg.teams.values()}):
            self.say(f"\n{conf:<28}{'W':>4}{'L':>4}{'NET':>7}")
            for tid in ranked(lg, [t.id for t in lg.teams.values() if t.conference == conf]):
                w, l, diff = rec[tid]
                mark = " <--" if tid == self.me.id else ""
                self.say(f"  {lg.teams[tid].full_name:<26}{w:>4}{l:>4}{diff / max(1, w + l):>+7.1f}{mark}")

    def cmd_leaders(self, args):
        lg = self.league
        totals = season_totals(lg)
        for stat, label in (("pts", "Points"), ("trb", "Rebounds"), ("ast", "Assists"),
                            ("stl", "Steals"), ("blk", "Blocks")):
            cells = [f"{player_label(lg, pid)} {v:.1f}" for pid, v in leaders(lg, totals, stat, 5)]
            self.say(f"  {label:<9}" + " | ".join(cells) if cells else f"  {label}: not enough games yet")

    def cmd_schedule(self, args):
        lg = self.league
        upcoming = sorted((g for g in lg.games.values() if g.season == lg.season and not g.played
                           and self.me.id in (g.home_team_id, g.away_team_id)), key=lambda g: g.day)[:7]
        for g in upcoming:
            home = g.home_team_id == self.me.id
            opp = lg.teams[g.away_team_id if home else g.home_team_id]
            self.say(f"  day {g.day:>3}: {'vs' if home else '@ '} {opp.full_name} ({self.record(opp.id)})")
        if not upcoming:
            self.say("  No games left in the regular season.")

    def cmd_results(self, args):
        lg = self.league
        done = sorted((g for g in lg.games.values() if g.season == lg.season and g.played
                       and self.me.id in (g.home_team_id, g.away_team_id)), key=lambda g: g.day)[-10:]
        for g in done:
            self.say("  " + self.result_line(g))
        if not done:
            self.say("  No games played yet.")

    def cmd_teams(self, args):
        lg = self.league
        for t in sorted(lg.teams.values(), key=lambda t: t.abbreviation):
            self.say(f"  {t.abbreviation}  {t.full_name:<26} {self.record(t.id):>7}  {t.conference}")

    def cmd_history(self, args):
        lg = self.league
        if not lg.history:
            self.say("No completed seasons yet.")
        for h in lg.history:
            self.say(f"  {h['season']}: {lg.teams[h['champion']].full_name} ({h['finals']}), "
                     f"MVP {h['award_names']['mvp']}")

    def cmd_save(self, args):
        path = args[0] if args else self.save_path
        save_league(self.league, path)
        self.save_path = path
        self.say(f"Saved to {path}")

    def cmd_help(self, args):
        self.say(HELP)


def start(args, io: Optional[IO] = None) -> None:
    io = io or IO()
    if args.load:
        league = load_league(args.load)
        if league.user_team_id is None:
            raise SystemExit("that save has no user team; start a new game instead")
        if league.phase is Phase.OFFSEASON:
            raise SystemExit("that save is mid-offseason; continue it with the career command")
    else:
        if getattr(args, "league", None):
            from .league_file import load_league_file
            league = load_league_file(args.league, seed=args.seed or 0)
        else:
            league = generate_league(seed=args.seed, num_teams=args.teams)
        team = None
        if args.team:
            team = next((t for t in league.teams.values() if t.abbreviation.lower() == args.team.lower()), None)
        while team is None:
            io.say("\nChoose your team:")
            for t in sorted(league.teams.values(), key=lambda t: t.abbreviation):
                top = [p.current_ability for p in league.roster(t.id)[:8]]
                io.say(f"  {t.abbreviation}  {t.full_name:<26} {t.conference:<5} top-8 CA {sum(top) / len(top):5.1f}  "
                       f"payroll {money(league.payroll(t.id))}")
            a = io.ask("Team abbreviation: ").strip()
            team = next((t for t in league.teams.values() if t.abbreviation.lower() == a.lower()), None)
        choose_team(league, team.id)
    if getattr(args, "engine", None):
        league.rules.match_engine = args.engine          # choose (or switch) the match engine
    rng = random.Random(args.seed if args.seed is not None else None)
    Game(league, io, rng, save_path=args.load or args.out or "hoopsim_save.db").run()
