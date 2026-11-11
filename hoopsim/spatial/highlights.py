"""Match highlights: how much each recorded possession matters to watch.

The viewer's highlight modes use this (as in Football Manager):

* **Key** (``hl == 2``): the plays that decide or define a game. Lead changes and
  ties, the basket that completes an 8-0 run, alley-oops, and-ones, buzzer-beaters,
  the play out of a timeout, and everything in a close finish (the last five
  minutes of the 4th quarter or overtime, within eight).
* **Extended** (``hl >= 1``): key moments plus made threes, transition baskets,
  baskets that take the lead, steals, blocks, charges, putbacks and the coaches'
  timeouts. Routine twos and free-throw trips are skipped.
* **Full**: every possession.

It only reads what a possession recorded (scores, events, the game clock in the
frames, live bench notes), so it never changes a game. ``annotate`` is idempotent
and works on old replays too.
"""
from __future__ import annotations

ROUTINE, EXTENDED, KEY = 0, 1, 2
CLOSE_FINISH_SECONDS = 300.0       # last five minutes of the 4th quarter or overtime...
CLOSE_FINISH_MARGIN = 8            # ...within this many points
RUN_KEY = 8                        # an 8-0 run is a key moment (6-0 is shown in extended)

_KEY_WORDS = ("alley-oop", "and one", "at the buzzer", "out of the timeout")
_EXTENDED_WORDS = ("blocked", "picked off", "strips", "pokes it away", "breaks up the lob", "stolen!",
                   "charge!", "offensive rebound", "timeout")


def _clock_at_end(p: dict):
    frames = p.get("frames") or []
    if frames and len(frames[-1]) > 26:
        return frames[-1][26]
    return None


def _sign(x: int) -> int:
    return (x > 0) - (x < 0)


def classify(p: dict, run: dict) -> tuple[int, str]:
    """(level, short reason) for one possession; ``run`` carries scoring-run state
    across calls ({"side": "home"|"away"|None, "pts": int})."""
    home0, away0 = p["start_score"]
    home1, away1 = p["end_score"]
    side = p["offense"]
    pts = (home1 - home0) if side == "home" else (away1 - away0)
    conceded = (away1 - away0) if side == "home" else (home1 - home0)
    text = " ".join(str(e[1]) for e in p.get("events", [])).lower()
    text += " " + " ".join(p.get("notes", [])).lower()

    # Scoring runs (points since the other side last scored).
    if conceded > 0:
        run["side"], run["pts"] = ("away" if side == "home" else "home"), conceded
    if pts > 0:
        if run.get("side") == side:
            run["pts"] += pts
        else:
            run["side"], run["pts"] = side, pts
    run_pts = run["pts"] if run.get("side") == side and pts > 0 else 0

    before, after = _sign(home0 - away0), _sign(home1 - away1)
    lead_change = pts > 0 and before != 0 and after != before        # took the lead or tied it
    took_lead = pts > 0 and before == 0 and after != 0
    clock = _clock_at_end(p)
    close_finish = (p.get("period", 0) >= 4 and clock is not None and clock <= CLOSE_FINISH_SECONDS
                    and abs(home1 - away1) <= CLOSE_FINISH_MARGIN)

    if close_finish:
        return KEY, "close finish"
    if lead_change:
        return KEY, "tie game" if after == 0 else "lead change"
    if run_pts >= RUN_KEY and run_pts - pts < RUN_KEY:
        return KEY, f"{run_pts}-0 run"
    for w in _KEY_WORDS:
        if w in text and (pts > 0 or w == "out of the timeout"):
            return KEY, w
    if took_lead:
        return EXTENDED, "takes the lead"
    made_three = pts >= 3 and "fires a three" in text
    if made_three:
        return EXTENDED, "three"
    if pts > 0 and p.get("transition"):
        return EXTENDED, "fast break"
    if pts > 0 and "offensive rebound" in text:
        return EXTENDED, "putback"
    for w in _EXTENDED_WORDS:
        if w in text:
            return EXTENDED, w
    return ROUTINE, ""


def annotate(possessions: list[dict], start: int = 0) -> list[dict]:
    """Set ``hl`` and ``hl_why`` on ``possessions[start:]`` (earlier ones are read for
    the run state). Returns the list."""
    run = {"side": None, "pts": 0}
    for i, p in enumerate(possessions):
        level, why = classify(p, run)
        if i >= start:
            p["hl"], p["hl_why"] = level, why
    return possessions
