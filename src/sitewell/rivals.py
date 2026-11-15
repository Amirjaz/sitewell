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
from urllib.parse import urljoin, urlsplit

from .web import Fetcher, parse_page, read_sitemaps

HOME_LINKS_LIMIT = 300

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


def home_links(fetcher: Fetcher, base: str) -> tuple[dict[str, str | None], str | None]:
    """Same-site links on the home page: the page list for a site with no readable sitemap."""
    got = fetcher.get(base + "/")
    if not got.ok:
        return {}, got.error or f"home page answered {got.status}"
    from bs4 import BeautifulSoup

    host = urlsplit(got.final_url or base).netloc.lower().removeprefix("www.")
    urls: dict[str, str | None] = {}
    for a in BeautifulSoup(got.text, "html.parser").find_all("a", href=True):
        url = urljoin(got.final_url or base, a["href"]).split("#")[0]
        if urlsplit(url).netloc.lower().removeprefix("www.") == host and url.startswith("http"):
            urls.setdefault(url, None)
        if len(urls) >= HOME_LINKS_LIMIT:
            break
    return urls, None


def _why(errors: list[str]) -> str:
    if errors and all("robots.txt" in e for e in errors):
        return "blocked-by-robots"
    if errors and all(e.endswith(": 404") for e in errors):
        return "no-sitemap"
    return "unreachable" if errors else "no-sitemap"


def scan(fetcher: Fetcher, domain: str, previous: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (new snapshot, this week's report)."""
    base = f"https://{domain}"
    sm = read_sitemaps(fetcher, base)
    source = "sitemap"
    if not sm.urls and _why(sm.errors) == "no-sitemap":
        links, err = home_links(fetcher, base)
        if links:
            sm.urls, source = links, "home-page links"
        elif err:
            sm.errors.append(err)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    snapshot = {"domain": domain, "scanned": now, "sources": sm.sources, "list_from": source, "urls": sm.urls}
    report: dict[str, Any] = {"domain": domain, "listed": len(sm.urls), "list_from": source, "errors": sm.errors, "truncated": sm.truncated}
    if not sm.urls:
        report["status"] = _why(sm.errors)
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
