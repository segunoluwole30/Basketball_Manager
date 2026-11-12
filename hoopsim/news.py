"""The manager's inbox: news items stored on the league (shown by the web app)."""
from __future__ import annotations

from .models import League

INBOX_LIMIT = 300


def post(league: League, kind: str, title: str, body: list[str], **extra) -> dict:
    next_id = max((m["id"] for m in league.inbox), default=0) + 1
    item = {"id": next_id, "date": league.current_date.isoformat(), "season": league.season,
            "kind": kind, "title": title, "body": body, "read": False, **extra}
    league.inbox.append(item)
    del league.inbox[:-INBOX_LIMIT]
    return item
