"""The change ledger: every change shipped to a site, and what it did after 14 and 28 days.

One JSON object per line in `ledger.jsonl`, appended, never rewritten except to add the
judgements. A change is judged against its own page's numbers before it shipped, and the
page's move is divided by the whole site's move over the same days, so a site-wide rise or
an update does not get credited to (or blamed on) one edit.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .web import normalise_url

HORIZONS = (14, 28)
KINDS = ("title", "description", "internal-links", "schema", "text", "new-page", "technical", "other")
GAIN, LOSS = 1.25, 0.80  # page move relative to the site's move
MIN_CLICKS, MIN_IMPRESSIONS = 20, 200  # below both, a verdict would be noise


@dataclass
class Change:
    id: str
    site: str
    url: str
    kind: str
    shipped: str  # ISO date the change went live
    summary: str
    before: str | None = None
    after: str | None = None
    ref: str | None = None  # commit, post revision, pull request
    source: str = "sitewell"  # sitewell | imported
    judged: dict[str, Any] = field(default_factory=dict)

    @property
    def shipped_on(self) -> date:
        return date.fromisoformat(self.shipped)


def load(path: Path) -> list[Change]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(Change(**json.loads(line)))
    return out


def save(changes: list[Change], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(asdict(c), ensure_ascii=False, sort_keys=True) + "\n" for c in changes)
    path.write_text(text, encoding="utf-8")


def add(path: Path, change: Change) -> None:
    if change.kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    changes = load(path)
    if any(c.id == change.id for c in changes):
        raise ValueError(f"a change with id {change.id} is already in the ledger")
    change.url = normalise_url(change.url)
    changes.append(change)
    save(changes, path)


def import_file(path: Path, source: Path) -> tuple[int, list[str]]:
    """Add the changes in a JSONL file to the ledger. Returns (added, problems). Existing ids are skipped."""
    changes = load(path)
    have = {c.id for c in changes}
    added, problems = 0, []
    for n, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            data.setdefault("judged", {})
            c = Change(**data)
            c.shipped_on  # a real date
        except (ValueError, TypeError) as exc:
            problems.append(f"line {n}: {exc}")
            continue
        if c.kind not in KINDS:
            problems.append(f"line {n}: unknown kind {c.kind!r}")
            continue
        if c.id in have:
            continue
        c.url = normalise_url(c.url)
        changes.append(c)
        have.add(c.id)
        added += 1
    changes.sort(key=lambda c: (c.shipped, c.id))
    save(changes, path)
    return added, problems


def _sums(frame, start: date, end: date) -> tuple[float, float, float | None]:
    import pandas as pd

    part = frame[(frame["date"] >= pd.Timestamp(start)) & (frame["date"] <= pd.Timestamp(end))]
    clicks, imp = float(part["clicks"].sum()), float(part["impressions"].sum())
    pos = float((part["position"] * part["impressions"]).sum() / imp) if imp else None
    return clicks, imp, pos


def judge_one(change: Change, horizon: int, pages, site_daily, data_end: date) -> dict[str, Any] | None:
    """The verdict at `horizon` days, or None while those days are not all in the data yet."""
    shipped = change.shipped_on
    after_start, after_end = shipped + timedelta(days=1), shipped + timedelta(days=horizon)
    if after_end > data_end:
        return None
    before_start, before_end = shipped - timedelta(days=horizon), shipped - timedelta(days=1)
    page = pages[pages["key"] == change.url]
    pc0, pi0, pp0 = _sums(page, before_start, before_end)
    pc1, pi1, pp1 = _sums(page, after_start, after_end)
    sc0, si0, _ = _sums(site_daily, before_start, before_end)
    sc1, si1, _ = _sums(site_daily, after_start, after_end)
    if pc0 + pc1 >= MIN_CLICKS:
        metric, a, b, sa, sb = "clicks", pc1, pc0, sc1, sc0
    elif pi0 + pi1 >= MIN_IMPRESSIONS:
        metric, a, b, sa, sb = "impressions", pi1, pi0, si1, si0
    else:
        metric, a, b, sa, sb = None, pc1, pc0, sc1, sc0
    site_ratio = sa / sb if sb else None
    if metric is None or not b or not site_ratio:
        verdict, relative = "too little data", None
    else:
        relative = (a / b) / site_ratio
        expected = b * site_ratio
        noise = 2 * math.sqrt(expected + 1)  # two standard deviations, counting events as Poisson
        if relative >= GAIN and a - expected > noise:
            verdict = "gain"
        elif relative <= LOSS and expected - a > noise:
            verdict = "loss"
        else:
            verdict = "no clear change"
    return {
        "judged_on": data_end.isoformat(),
        "metric": metric,
        "page": {"clicks": [round(pc0), round(pc1)], "impressions": [round(pi0), round(pi1)],
                 "position": [round(pp0, 1) if pp0 else None, round(pp1, 1) if pp1 else None]},
        "site_ratio": round(site_ratio, 3) if site_ratio else None,
        "relative_to_site": round(relative, 3) if relative else None,
        "verdict": verdict,
    }


def judge_all(changes: list[Change], an, data_end: date) -> list[dict[str, Any]]:
    """Fill in every judgement that has come due. Returns the ones added this time."""
    import pandas as pd

    pages = None
    for t in an.ds.flats:
        if tuple(t.dims) == ("date", "page"):
            pages = t.data
    daily = an.ds.daily()
    if pages is None or daily is None:
        return []
    pages = pages.assign(date=pd.to_datetime(pages["date"]), key=pages["page"].map(normalise_url))
    site_daily = daily.data.reset_index().rename(columns={"index": "date"})
    site_daily["date"] = pd.to_datetime(site_daily["date"])
    added = []
    for c in changes:
        for h in HORIZONS:
            if str(h) in c.judged:
                continue
            result = judge_one(c, h, pages, site_daily, data_end)
            if result is not None:
                c.judged[str(h)] = result
                added.append({"id": c.id, "url": c.url, "kind": c.kind, "days": h, **result})
    return added
