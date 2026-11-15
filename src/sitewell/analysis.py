"""Turn a Search Console bundle into the week's facts.

The detectors are gsc-pulse's: bot-click bursts, traffic shifts matched to Google updates,
CTR gaps, striking distance, cannibalisation, movers. This module feeds them the bundle,
adds what an agent needs on top (a non-brand baseline and the evidence for new pages), and
flattens everything into plain JSON. Every number here is computed; nothing is estimated
by a model.
"""

from __future__ import annotations

import csv
import dataclasses
import math
import re
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .config import Site

TOP = 15
GATE_MIN_IMPRESSIONS = 30  # over 28 days, for a query to count as real demand
GATE_WEAK_POSITION = 15.0  # best page ranks below this: nothing on the site really answers it


# ------------------------------------------------------------------- plain JSON ---


def plain(value: Any) -> Any:
    """Dataclasses, dates and numpy numbers to JSON-ready values. NaN and infinity become None."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value) if not _skip(getattr(value, f.name))}
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items() if not _skip(v)}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):  # numpy scalar
        try:
            value = value.item()
        except (ValueError, AttributeError):
            return None
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else round(value, 4)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if hasattr(value, "start") and hasattr(value, "end"):  # DateRange
        return {"start": plain(value.start), "end": plain(value.end)}
    return str(value)


def _skip(value: Any) -> bool:
    mod = type(value).__module__
    return mod.startswith("pandas") or (mod.startswith("numpy") and getattr(value, "ndim", 0) > 0)


# ----------------------------------------------------------------- preparation ---


def prepare(raw: Path, work: Path, site: Site) -> dict[str, Any]:
    """Copy the bundle into `work`, dropping queries the site says to ignore."""
    work.mkdir(parents=True, exist_ok=True)
    dropped: dict[str, Any] = {"rows": 0, "clicks": 0, "by_day": {}}
    for src in sorted(raw.glob("*.csv")):
        dst = work / src.name
        if site.ignore_query_patterns and src.stem in ("queries", "query_pages"):
            with src.open(encoding="utf-8", newline="") as fin, dst.open("w", encoding="utf-8", newline="") as fout:
                reader = csv.DictReader(fin)
                writer = csv.DictWriter(fout, fieldnames=reader.fieldnames or [])
                writer.writeheader()
                for row in reader:
                    if site.ignored(row.get("query", "")):
                        if src.stem == "queries":
                            clicks = float(row.get("clicks") or 0)
                            dropped["rows"] += 1
                            dropped["clicks"] += int(clicks)
                            day = row.get("date", "")
                            dropped["by_day"][day] = dropped["by_day"].get(day, 0.0) + clicks
                        continue
                    writer.writerow(row)
        else:
            shutil.copyfile(src, dst)
    return dropped


def gsc_config(site: Site):
    from gsc_pulse.config import Config

    return Config(site_name=site.domain, brand_terms=list(site.brand_terms), brand_patterns=list(site.brand_patterns))


def run_detectors(work: Path, site: Site):
    from gsc_pulse.report import analyse

    return analyse(work, gsc_config(site))


# ------------------------------------------------------------------ the facts ---


def _flat(ds, dims: tuple[str, ...]):
    for t in ds.flats:
        if tuple(t.dims) == dims:
            return t.data
    return None


def _home(url: str) -> bool:
    return re.sub(r"^https?://[^/]+", "", url or "").strip("/") == ""


def baseline(an, ignored_clicks_by_day: dict[str, float] | None = None) -> dict[str, Any]:
    """Weekly clicks with bot bursts taken out, split into brand and non-brand.

    Non-brand = all clicks minus clicks on brand queries. Clicks Search Console hides
    (anonymised queries) count as non-brand, which is what they nearly always are.
    """
    import pandas as pd

    daily_src = an.ds.daily()
    if daily_src is None:
        return {"status": "no-data"}
    daily = daily_src.data
    cleaned = an.bots.cleaned_clicks(daily)
    brand = pd.Series(0.0, index=daily.index)
    q = _flat(an.ds, ("date", "query"))
    if q is not None and an.brand.active:
        b = q[an.brand.mask(q["query"])]
        if not b.empty:
            per_day = b.groupby(pd.to_datetime(b["date"]))["clicks"].sum()
            brand = brand.add(per_day, fill_value=0.0).reindex(daily.index, fill_value=0.0)
    ignored = pd.Series(0.0, index=daily.index)
    if ignored_clicks_by_day:
        ig = pd.Series({pd.Timestamp(k): v for k, v in ignored_clicks_by_day.items()})
        ignored = ignored.add(ig, fill_value=0.0).reindex(daily.index, fill_value=0.0)
    nonbrand = (cleaned - brand - ignored).clip(lower=0.0)
    frame = pd.DataFrame(
        {"clicks": daily["clicks"], "cleaned": cleaned, "brand": brand, "nonbrand": nonbrand, "impressions": daily["impressions"]}
    )
    weeks = []
    for _, part in frame.resample("W-SUN"):  # ISO weeks, Monday to Sunday
        if len(part) < 7:
            continue  # only whole weeks, so weeks compare like for like
        first = part.index.min().date()
        iso = first.isocalendar()
        weeks.append(
            {
                "week": f"{iso.year}-W{iso.week:02d}",
                "start": first.isoformat(),
                "clicks": round(float(part["clicks"].sum())),
                "bot_excess": round(float((part["clicks"] - part["cleaned"]).sum())),
                "brand": round(float(part["brand"].sum())),
                "nonbrand": round(float(part["nonbrand"].sum())),
                "impressions": round(float(part["impressions"].sum())),
            }
        )

    def median(rows):
        values = sorted(r["nonbrand"] for r in rows)
        if not values:
            return None
        mid = len(values) // 2
        return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2

    return {
        "status": "ok",
        "weeks": weeks,
        "nonbrand_median_last_8w": median(weeks[-8:]),
        "nonbrand_median_prior_8w": median(weeks[-16:-8]),
        "nonbrand_median_year_ago_8w": median(weeks[-60:-52]) if len(weeks) >= 60 else None,
        "note": "Non-brand = clicks minus brand-query clicks, with bot-burst days replaced by their expected level.",
    }


def gate_candidates(an, end: date, days: int = 28) -> dict[str, Any]:
    """Queries with real demand and no page on the site that really answers them."""
    import pandas as pd

    qp = _flat(an.ds, ("date", "query", "page"))
    if qp is None or qp.empty:
        return {"status": "no-data", "items": []}
    start = end - timedelta(days=days - 1)
    part = qp[(pd.to_datetime(qp["date"]) >= pd.Timestamp(start)) & (pd.to_datetime(qp["date"]) <= pd.Timestamp(end))]
    part = part[~an.brand.mask(part["query"])] if an.brand.active else part
    if part.empty:
        return {"status": "ok", "items": [], "window": {"start": start.isoformat(), "end": end.isoformat()}}
    work = part.assign(_pw=part["position"] * part["impressions"])
    g = work.groupby(["query", "page"], as_index=False)[["clicks", "impressions", "_pw"]].sum()
    g["position"] = g["_pw"] / g["impressions"].where(g["impressions"] > 0)
    items = []
    for query, rows in g.groupby("query"):
        total_imp = float(rows["impressions"].sum())
        if total_imp < GATE_MIN_IMPRESSIONS:
            continue
        best = rows.sort_values(["position", "impressions"], ascending=[True, False]).iloc[0]
        reasons = []
        if best["position"] > GATE_WEAK_POSITION:
            reasons.append(f"best page ranks {best['position']:.1f}")
        if _home(best["page"]) and len(rows) == 1:
            reasons.append("only the home page shows")
        if not reasons:
            continue
        items.append(
            {
                "query": query,
                "impressions": round(total_imp),
                "clicks": round(float(rows["clicks"].sum())),
                "best_page": best["page"],
                "best_position": round(float(best["position"]), 1),
                "pages": len(rows),
                "why": "; ".join(reasons),
            }
        )
    items.sort(key=lambda r: -r["impressions"])
    return {
        "status": "ok",
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "rule": f"non-brand, at least {GATE_MIN_IMPRESSIONS} impressions in {days} days, and the best page ranks below {GATE_WEAK_POSITION:g} or only the home page shows",
        "items": items[:25],
    }


def _burst(b) -> dict[str, Any]:
    return {
        "start": b.window.start.isoformat(),
        "end": b.window.end.isoformat(),
        "clicks": round(b.clicks),
        "expected": round(b.expected_clicks),
        "excess": round(b.excess_clicks),
        "verdict": b.verdict,
        "max_z": round(b.max_z, 1),
        "concentrated_in": [{"dimension": d, "value": v, "share": round(s, 2)} for d, v, s in b.concentrated_in],
    }


def _shift(s) -> dict[str, Any]:
    return {
        "first_day": s.first_day.isoformat(),
        "direction": s.direction,
        "change": plain(s.change),
        "before_per_day": round(s.before_mean, 1),
        "after_per_day": round(s.after_mean, 1),
        "last_28_days_per_day": round(s.recent_mean, 1),
        "recovered": s.recovered,
        "open_loss": s.open_loss,
        "google_updates": [u.name for u in s.updates],
    }


def _mover(m) -> dict[str, Any]:
    return {
        "key": m.key,
        "clicks": [round(m.clicks_prev), round(m.clicks_cur)],
        "impressions": [round(m.impressions_prev), round(m.impressions_cur)],
        "position": [plain(m.position_prev), plain(m.position_cur)],
        "reason": m.reason,
    }


def updates_view(an, end: date) -> dict[str, Any]:
    recent = []
    ongoing = []
    for u in an.updates:
        last = u.end or end
        if u.ongoing:
            ongoing.append({"name": u.name, "kind": u.kind, "start": u.start.isoformat()})
        if last >= end - timedelta(days=90):
            recent.append({"name": u.name, "kind": u.kind, "start": u.start.isoformat(), "end": plain(u.end)})
    return {
        "ongoing": ongoing,
        "last_90_days": recent,
        "source": "gsc-pulse's dated list of Google updates; refresh it before trusting 'ongoing'",
    }


def build_facts(an, site: Site, meta: dict[str, Any], dropped: dict[str, Any]) -> dict[str, Any]:
    end = date.fromisoformat(meta["end"])
    k = an.kpis
    kpis = None
    if k is not None:
        kpis = {
            "current": plain(k.current),
            "previous": plain(k.previous),
            "change": {m: plain(k.change(m)) for m in ("clicks", "impressions", "ctr", "position")},
            "bot_excess": {"current": round(k.bot_excess_current), "previous": round(k.bot_excess_previous)},
            "click_change_without_bots": plain(k.adjusted_click_change) if k.adjusted else None,
        }
    notes = list(dict.fromkeys([*an.ds.notes, *an.ctx.notes]))
    if dropped.get("rows"):
        notes.append(f"{dropped['rows']} query rows ({dropped['clicks']} clicks) matched ignore_query_patterns and were left out")
    return {
        "site": site.name,
        "property": site.property,
        "data": {"start": meta["start"], "end": meta["end"], "rows": meta["rows"]},
        "periods": plain({"current": an.periods.current, "previous": an.periods.previous}) if an.periods else None,
        "kpis": kpis,
        "brand_click_share": plain(an.ctx.brand_clicks_share),
        "baseline": baseline(an, dropped.get("by_day")),
        "bot_bursts": [_burst(b) for b in an.bots.bursts],
        "traffic_shifts": [_shift(s) for s in an.drops.shifts],
        "google_updates": updates_view(an, end),
        "ctr_gaps": [plain(g) for g in an.gaps.items[:TOP]],
        "striking_distance": [plain(s) for s in an.striking.items[:TOP]],
        "cannibalisation": [plain(c) for c in an.cannibal.items[:TOP]],
        "movers": {
            kind: {"gainers": [_mover(m) for m in ms.gainers[:10]], "losers": [_mover(m) for m in ms.losers[:10]]}
            for kind, ms in an.movers.items()
            if kind in ("queries", "pages")
        },
        "top_actions": [plain(a) for a in an.actions],
        "new_page_evidence": gate_candidates(an, end),
        "notes": notes,
    }


def monthly(an, ignored_clicks_by_day: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Calendar-month totals, so the long-term trend survives Search Console's 16-month limit."""
    import pandas as pd

    daily_src = an.ds.daily()
    if daily_src is None:
        return []
    daily = daily_src.data
    cleaned = an.bots.cleaned_clicks(daily)
    brand = pd.Series(0.0, index=daily.index)
    q = _flat(an.ds, ("date", "query"))
    if q is not None and an.brand.active:
        b = q[an.brand.mask(q["query"])]
        if not b.empty:
            brand = brand.add(b.groupby(pd.to_datetime(b["date"]))["clicks"].sum(), fill_value=0.0).reindex(daily.index, fill_value=0.0)
    frame = pd.DataFrame({"clicks": daily["clicks"], "cleaned": cleaned, "brand": brand, "impressions": daily["impressions"]})
    first, last = daily.index.min(), daily.index.max()
    out = []
    for month, part in frame.groupby(frame.index.to_period("M")):
        start, end = month.start_time.normalize(), month.end_time.normalize()
        out.append(
            {
                "month": str(month),
                "days": len(part),
                "complete": bool(start >= first and end <= last),
                "clicks": round(float(part["clicks"].sum())),
                "bot_excess": round(float((part["clicks"] - part["cleaned"]).sum())),
                "brand_clicks": round(float(part["brand"].sum())),
                "impressions": round(float(part["impressions"].sum())),
            }
        )
    return out
