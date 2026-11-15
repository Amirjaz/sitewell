"""What the site's named rivals published or changed since last week.

Each rival's sitemap is read and compared with last week's snapshot. New and updated pages
are then read (a few per rival, newest first) for what they are: title, headings, length,
structured data. Nothing is copied: the report describes pages, it never stores their text.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .web import Fetcher, parse_page, read_sitemaps

PAGES_PER_RIVAL = 12
FIRST_LOOK_PAGES = 8


def load_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _describe(fetcher: Fetcher, url: str) -> dict[str, Any]:
    got = fetcher.get(url)
    if not got.ok or "html" not in got.content_type:
        return {"url": url, "status": got.status, "error": got.error or ("not HTML" if got.ok else None)}
    info = parse_page(got.text, got.final_url or url)
    return {
        "url": url,
        "status": got.status,
        "title": info["title"],
        "h1": info["h1"][:2],
        "h2": info["h2"][:15],
        "h2_count": info["h2_count"],
        "words": info["words"],
        "schema_types": info["schema_types"],
        "published": info["published"],
        "modified": info["modified"],
    }


def scan(fetcher: Fetcher, domain: str, previous: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (new snapshot, this week's report)."""
    base = f"https://{domain}"
    sm = read_sitemaps(fetcher, base)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    snapshot = {"domain": domain, "scanned": now, "sources": sm.sources, "urls": sm.urls}
    report: dict[str, Any] = {"domain": domain, "listed": len(sm.urls), "errors": sm.errors}
    if not sm.urls:
        report["status"] = "unreachable" if sm.errors else "no-sitemap"
        if previous:  # keep last week's list so one bad week doesn't make everything "new" next time
            snapshot["urls"] = previous.get("urls", {})
            snapshot["kept_from"] = previous.get("scanned")
        return snapshot, report

    def newest(urls):
        return sorted(urls, key=lambda u: sm.urls.get(u) or "", reverse=True)

    if previous is None or not previous.get("urls"):
        report["status"] = "first-look"
        report["pages"] = [_describe(fetcher, u) for u in newest(sm.urls)[:FIRST_LOOK_PAGES]]
        return snapshot, report

    before = previous["urls"]
    new = [u for u in sm.urls if u not in before]
    updated = [u for u in sm.urls if u in before and sm.urls[u] and sm.urls[u] != before[u]]
    removed = [u for u in before if u not in sm.urls]
    report.update(status="ok", new=len(new), updated=len(updated), removed=len(removed))
    picks = newest(new)[:PAGES_PER_RIVAL]
    picks += newest(updated)[: max(0, PAGES_PER_RIVAL - len(picks))]
    report["pages"] = [{**_describe(fetcher, u), "change": "new" if u in new else "updated"} for u in picks]
    report["removed_sample"] = removed[:10]
    return snapshot, report


def save_snapshot(snapshot: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
