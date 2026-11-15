"""Google's ranking updates, read live from the Search Status Dashboard.

gsc-pulse ships a dated list, but it is a copy and goes stale. The live feed decides whether
an update is rolling out right now, which is one of the reasons sitewell pauses changes.
"""

from __future__ import annotations

from datetime import date
from typing import Any

FEED = "https://status.search.google.com/incidents.json"


def _kind(name: str) -> str:
    n = name.lower()
    for key in ("core", "spam", "helpful content", "reviews"):
        if key in n:
            return key.replace(" ", "-")
    return "other"


def parse(incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ranking incidents as gsc-pulse update entries: name, kind, start, end (None while rolling out)."""
    out = []
    for i in incidents:
        if (i.get("service_name") or "").lower() != "ranking" or not i.get("begin"):
            continue
        name = (i.get("external_desc") or "").strip() or f"Ranking update {i['begin'][:10]}"
        end = i.get("end")
        out.append(
            {
                "name": name,
                "kind": _kind(name),
                "start": date.fromisoformat(i["begin"][:10]),
                "end": date.fromisoformat(end[:10]) if end else None,
            }
        )
    return out


def fetch(session=None, timeout: float = 20.0) -> tuple[list[dict[str, Any]], str | None]:
    """(entries, error). On any failure the entries are empty and the built-in list stands."""
    if session is None:
        import requests

        session = requests
    try:
        r = session.get(FEED, timeout=timeout)
        r.raise_for_status()
        return parse(r.json()), None
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"[:200]
