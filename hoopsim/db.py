"""SQLite persistence.

Each entity is stored as a JSON blob in a ``data`` column. A few columns
(team, position, season...) are copied out of the blob so you can query and
index them in SQL, e.g.::

    SELECT json_extract(data, '$.attributes.three_point') AS tp, data
    FROM players WHERE position = 'SG' ORDER BY tp DESC LIMIT 10;
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from .models import Game, League, LeagueRules, Phase, Player, Series, Team

SCHEMA_VERSION = 4   # v2 adds phase, playoffs and history; v3 an offseason in progress and the inbox;
                     # v4 replays of the user's spatial games
READABLE_VERSIONS = (1, 2, 3, 4)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS teams (
    id           INTEGER PRIMARY KEY,
    abbreviation TEXT NOT NULL UNIQUE,
    data         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS players (
    id        INTEGER PRIMARY KEY,
    team_id   INTEGER REFERENCES teams(id),
    last_name TEXT NOT NULL,
    position  TEXT NOT NULL,
    data      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS players_team ON players(team_id);
CREATE TABLE IF NOT EXISTS games (
    id           INTEGER PRIMARY KEY,
    season       INTEGER NOT NULL,
    day          INTEGER NOT NULL,
    home_team_id INTEGER NOT NULL REFERENCES teams(id),
    away_team_id INTEGER NOT NULL REFERENCES teams(id),
    playoff      INTEGER NOT NULL,
    data         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS games_season_day ON games(season, day);
CREATE TABLE IF NOT EXISTS replays (
    game_id INTEGER PRIMARY KEY,
    data    BLOB NOT NULL
);
"""


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def save_league(league: League, path: str | Path) -> None:
    """Write the whole league to ``path``, replacing anything already there."""
    conn = _connect(path)
    try:
        with conn:  # one transaction: all or nothing
            conn.execute("DELETE FROM replays")
            conn.execute("DELETE FROM games")
            conn.execute("DELETE FROM players")
            conn.execute("DELETE FROM teams")
            conn.execute("DELETE FROM meta")
            meta = {
                "schema_version": str(SCHEMA_VERSION),
                "name": league.name,
                "season": str(league.season),
                "current_date": league.current_date.isoformat(),
                "rules": json.dumps(league.rules.to_dict()),
                "phase": league.phase.value,
                "playoffs": json.dumps([s.to_dict() for s in league.playoffs]),
                "history": json.dumps(league.history),
                "user_team_id": json.dumps(league.user_team_id),
                "mle_used": json.dumps(league.mle_used),
                "offseason": json.dumps(league.offseason),
                "last_offseason": json.dumps(league.last_offseason),
                "inbox": json.dumps(league.inbox),
            }
            conn.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
            conn.executemany(
                "INSERT INTO teams VALUES (?, ?, ?)",
                [(t.id, t.abbreviation, json.dumps(t.to_dict())) for t in league.teams.values()],
            )
            conn.executemany(
                "INSERT INTO players VALUES (?, ?, ?, ?, ?)",
                [(p.id, p.team_id, p.last_name, p.position.value, json.dumps(p.to_dict()))
                 for p in league.players.values()],
            )
            conn.executemany(
                "INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(g.id, g.season, g.day, g.home_team_id, g.away_team_id, int(g.playoff),
                  json.dumps(g.to_dict())) for g in league.games.values()],
            )
            conn.executemany("INSERT INTO replays VALUES (?, ?)",
                             [(gid, data) for gid, data in league.replays.items() if gid in league.games])
    finally:
        conn.close()


def load_league(path: str | Path) -> League:
    if not Path(path).exists():
        raise FileNotFoundError(path)
    conn = _connect(path)
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        version = int(meta.get("schema_version", 0))
        if version not in READABLE_VERSIONS:
            raise ValueError(f"save file schema v{version}, expected v{SCHEMA_VERSION}")
        league = League(
            name=meta["name"],
            season=int(meta["season"]),
            current_date=date.fromisoformat(meta["current_date"]),
            rules=LeagueRules.from_dict(json.loads(meta["rules"])),
            phase=Phase(meta.get("phase", Phase.REGULAR_SEASON.value)),
            playoffs=[Series.from_dict(s) for s in json.loads(meta.get("playoffs", "[]"))],
            history=json.loads(meta.get("history", "[]")),
            user_team_id=json.loads(meta.get("user_team_id", "null")),
            mle_used=json.loads(meta.get("mle_used", "[]")),
            offseason=_report_keys(json.loads(meta.get("offseason", "null"))),
            last_offseason=_report_keys(json.loads(meta.get("last_offseason", "null"))),
            inbox=json.loads(meta.get("inbox", "[]")),
        )
        for (data,) in conn.execute("SELECT data FROM teams ORDER BY id"):
            league.add_team(Team.from_dict(json.loads(data)))
        for (data,) in conn.execute("SELECT data FROM players ORDER BY id"):
            league.add_player(Player.from_dict(json.loads(data)))
        for (data,) in conn.execute("SELECT data FROM games ORDER BY id"):
            league.add_game(Game.from_dict(json.loads(data)))
        league.replays = {gid: bytes(data) for gid, data in conn.execute("SELECT game_id, data FROM replays")}
        return league
    finally:
        conn.close()


def _report_keys(state):
    """JSON turns the report's {player id: CA change} keys into strings: undo that."""
    if not state:
        return state
    report = state.get("report", state)
    if isinstance(report.get("development"), dict):
        report["development"] = {int(k): v for k, v in report["development"].items()}
    return state
