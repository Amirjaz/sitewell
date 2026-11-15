"""The site's own pages: what each one says about itself, joined with its search numbers."""

from __future__ import annotations

import csv
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .config import Site
from .web import Fetcher, normalise_url, parse_page, read_sitemaps

# Child sitemaps that list archives rather than content pages.
SKIP_SITEMAPS = ("category-sitemap", "post_tag-sitemap", "author-sitemap", "tag-sitemap", "format-sitemap", "attachment-sitemap")

COLUMNS = [
    "url", "status", "clicks_28d", "impressions_28d", "position_28d", "title", "title_length", "description_length",
    "h1", "h1_count", "h2_count", "words", "schema_types", "canonical", "noindex", "internal_links",
    "images_without_alt", "lastmod", "modified", "issues",
]


def page_numbers(an, end: date, days: int = 28) -> dict[str, dict[str, float]]:
    """Clicks, impressions and position per page over the last `days`, keyed by normalised URL."""
    import pandas as pd

    pages = None
    for t in an.ds.flats:
        if tuple(t.dims) == ("date", "page"):
            pages = t.data
    if pages is None or pages.empty:
        return {}
    start = pd.Timestamp(end - timedelta(days=days - 1))
    part = pages[pd.to_datetime(pages["date"]) >= start]
    part = part.assign(_pw=part["position"] * part["impressions"], key=part["page"].map(normalise_url))
    g = part.groupby("key")[["clicks", "impressions", "_pw"]].sum()
    out = {}
    for key, row in g.iterrows():
        imp = float(row["impressions"])
        out[key] = {
            "clicks": float(row["clicks"]),
            "impressions": imp,
            "position": round(float(row["_pw"] / imp), 1) if imp else None,
        }
    return out


def build(site: Site, fetcher: Fetcher, numbers: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Read the sitemap, then every page in it up to `site.max_pages`, busiest pages first."""
    sm = read_sitemaps(fetcher, site.base_url, skip=SKIP_SITEMAPS)
    listed = {normalise_url(u): (u, lastmod) for u, lastmod in sm.urls.items()}
    order = sorted(listed, key=lambda k: -(numbers.get(k, {}).get("impressions") or 0))
    rows: list[dict[str, Any]] = []
    for key in order[: site.max_pages]:
        url, lastmod = listed[key]
        got = fetcher.get(url)
        n = numbers.get(key, {})
        row: dict[str, Any] = {
            "url": key,
            "status": got.status if got.status is not None else (got.error or "failed"),
            "clicks_28d": round(n.get("clicks", 0)),
            "impressions_28d": round(n.get("impressions", 0)),
            "position_28d": n.get("position"),
            "lastmod": lastmod,
        }
        if got.ok and "html" in got.content_type:
            info = parse_page(got.text, got.final_url or url)
            row.update(
                title=info["title"],
                title_length=len(info["title"]),
                description_length=len(info["description"]),
                h1=" | ".join(info["h1"]),
                h1_count=len(info["h1"]),
                h2_count=info["h2_count"],
                words=info["words"],
                schema_types=", ".join(info["schema_types"]),
                canonical=normalise_url(info["canonical"]) if info["canonical"] else None,
                noindex=info["noindex"],
                internal_links=info["internal_links"],
                images_without_alt=info["images_without_alt"],
                modified=info["modified"],
                internal_targets=info["internal_targets"],
            )
        rows.append(row)

    titles = Counter(r.get("title") for r in rows if r.get("title"))
    kinds: Counter[str] = Counter()
    for r in rows:
        issues: list[tuple[str, str]] = []  # (kind, wording)
        if r["status"] != 200:
            issues.append(("not 200", f"answers {r['status']}"))
        elif r.get("title") is None:
            issues.append(("not HTML", "not HTML"))
        else:
            if not r["title"]:
                issues.append(("no title", "no title"))
            elif titles[r["title"]] > 1:
                issues.append(("shared title", "title shared with another page"))
            if not r.get("description_length"):
                issues.append(("no description", "no meta description"))
            if r.get("h1_count") != 1:
                issues.append(("h1 count", f"{r.get('h1_count')} h1 tags"))
            if r.get("noindex"):
                issues.append(("noindex in sitemap", "noindex but listed in the sitemap"))
            if r.get("canonical") and r["canonical"] != r["url"]:
                issues.append(("canonical elsewhere", "canonical points elsewhere"))
            if r.get("images_without_alt"):
                issues.append(("images without alt", f"{r['images_without_alt']} images without alt text"))
        kinds.update(k for k, _ in issues)
        r["issues"] = "; ".join(text for _, text in issues)

    unlisted = sorted(
        ({"url": k, **v} for k, v in numbers.items() if k not in listed and v.get("impressions", 0) >= 20),
        key=lambda r: -r["impressions"],
    )[:25]
    return {
        "sitemaps": sm.sources,
        "sitemap_errors": sm.errors,
        "listed": len(listed),
        "read": len(rows),
        "truncated": len(listed) > site.max_pages or sm.truncated,
        "pages": rows,
        "with_issues": sum(1 for r in rows if r["issues"]),
        "issue_counts": dict(kinds.most_common()),
        "searched_but_not_in_sitemap": unlisted,
    }


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
