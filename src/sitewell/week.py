"""One site's weekly data run: fetch, detect, read the site and its rivals, judge past changes.

Safe to run late, twice, or after a missed week: everything is recomputed from Search
Console's full window, the week's folder is overwritten as a whole, and the ledger only
gains judgements that have come due. Each step is recorded, and a failed step does not stop
the steps that don't depend on it.
"""

from __future__ import annotations

import csv
import json
import tempfile
import time
import traceback
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from . import __version__, analysis, gsc, inventory, ledger, rivals, runs, updates
from .config import Site, load_site
from .web import Fetcher

STEPS_THAT_MATTER = {"fetch search console", "detectors"}
RECENT_DAYS = 120  # the summary shows shifts and bursts from this far back; facts.json keeps all


def iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


class Steps:
    def __init__(self):
        self.done: list[dict[str, Any]] = []

    def run(self, name: str, fn: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        try:
            result = fn()
            self.done.append({"name": name, "status": "ok", "seconds": round(time.perf_counter() - started, 1)})
            return result
        except Exception as exc:
            self.done.append(
                {
                    "name": name,
                    "status": "failed",
                    "seconds": round(time.perf_counter() - started, 1),
                    "error": f"{type(exc).__name__}: {exc}"[:400],
                    "where": traceback.format_exc(limit=3)[-600:],
                }
            )
            return None

    def failed(self) -> list[str]:
        return [s["name"] for s in self.done if s["status"] == "failed"]


def update_history(path: Path, months: list[dict[str, Any]]) -> None:
    """Merge month rows into history/monthly.csv. Complete months win over partial ones."""
    rows: dict[str, dict[str, Any]] = {}
    if path.exists():
        with path.open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                rows[r["month"]] = r
    for m in months:
        old = rows.get(m["month"])
        if old is not None and str(old.get("complete")) == "True" and not m["complete"]:
            continue  # the window's first month is cut off: keep the full count from an earlier run
        rows[m["month"]] = m
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["month", "days", "complete", "clicks", "bot_excess", "brand_clicks", "impressions"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for key in sorted(rows):
            writer.writerow(rows[key])


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def summary_md(site: Site, facts: dict[str, Any] | None, inv: dict[str, Any] | None, rival_reports: list[dict[str, Any]], judged: list[dict[str, Any]], steps: Steps) -> str:
    lines = [f"# {site.name}: week of data", ""]
    if facts:
        d = facts["data"]
        cut = f" (earlier data ignored: data_start in site.toml)" if d.get("fetched_from") and d["start"] != d["fetched_from"] else ""
        lines.append(f"Search Console data {d['start']} to {d['end']}{cut}.")
        k = facts.get("kpis")
        if k:
            p = facts["periods"]
            lines += [
                "",
                f"## Last 28 days ({p['current']['start']} to {p['current']['end']}) against the 28 before",
                "",
                "| | Now | Before | Change |",
                "|---|---|---|---|",
                f"| Clicks | {k['current']['clicks']:,.0f} | {k['previous']['clicks']:,.0f} | {_pct(k['change']['clicks'])} |",
                f"| Impressions | {k['current']['impressions']:,.0f} | {k['previous']['impressions']:,.0f} | {_pct(k['change']['impressions'])} |",
                f"| Average position (lower is better) | {k['current']['position'] or 0:.1f} | {k['previous']['position'] or 0:.1f} | {k['change']['position'] or 0:+.1f} |",
            ]
            if k.get("click_change_without_bots") is not None:
                lines.append(f"| Clicks without bot bursts | | | {_pct(k['click_change_without_bots'])} |")
        b = facts.get("baseline", {})
        if b.get("status") == "ok":
            lines += ["", f"Non-brand clicks per week, median of the last 8 weeks: **{b['nonbrand_median_last_8w']}** (the 8 before: {b['nonbrand_median_prior_8w']})."]
        brands = facts.get("brand_queries") or []
        if brands:
            lines.append("Brand queries counted: " + ", ".join(f"{b['query']} ({b['clicks']})" for b in brands[:5]) + ".")
        recent_cut = (date.fromisoformat(facts["data"]["end"]) - timedelta(days=RECENT_DAYS)).isoformat()
        for burst in facts["bot_bursts"]:
            if burst["end"] >= recent_cut:
                lines.append(f"- Bot burst {burst['start']} to {burst['end']}: {burst['excess']} extra clicks, {burst['verdict']}.")
        recent = [s for s in facts["traffic_shifts"] if s["first_day"] >= recent_cut]
        for s in recent:
            ups = ", ".join(s["google_updates"]) or "no Google update nearby"
            lines.append(f"- Traffic {s['direction']} from {s['first_day']}: {_pct(s['change'])} ({ups}).")
        older = len(facts["traffic_shifts"]) - len(recent)
        if older:
            lines.append(f"- {older} earlier traffic shifts are in facts.json.")
        for w in facts.get("implausible_ctr_days") or []:
            lines.append(f"- Implausible click-through {w['start']} to {w['end']}: {w['clicks']} clicks from {w['impressions']} impressions ({w['ctr']:.0%}). Probably bots; consider data_start.")
        ongoing = facts["google_updates"]["ongoing"]
        if ongoing:
            lines.append(f"- **Google update rolling out: {', '.join(u['name'] + ' (since ' + u['start'] + ')' for u in ongoing)}.** Changes pause until it ends.")
        ev = facts["new_page_evidence"].get("items", [])
        if ev:
            lines += ["", "## Demand with no page that answers it", "", "| Query | Impressions | Best page | Position |", "|---|---|---|---|"]
            lines += [f"| {r['query']} | {r['impressions']} | {r['best_page']} | {r['best_position']} |" for r in ev[:10]]
        for title, key, cols in (
            ("Click-through gaps", "ctr_gaps", ("query", "position", "impressions", "missed")),
            ("Striking distance", "striking_distance", ("query", "position", "impressions", "potential")),
        ):
            rows = facts.get(key) or []
            if rows:
                lines += ["", f"## {title}", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
                for r in rows[:8]:
                    lines.append("| " + " | ".join(str(round(r[c], 1)) if isinstance(r.get(c), float) else str(r.get(c)) for c in cols) + " |")
        cann = facts.get("cannibalisation") or []
        if cann:
            lines += ["", "## Pages competing for one query", ""]
            lines += [f"- {c['query']}: {len(c['pages'])} pages" for c in cann[:8]]
    if inv:
        lines += ["", "## The site's pages", "", f"{inv['read']} of {inv['listed']} sitemap pages read; {inv['with_issues']} with something to fix."]
        lines += [f"- {kind}: {n}" for kind, n in inv["issue_counts"].items()]
        if inv["searched_but_not_in_sitemap"]:
            lines.append(f"- {len(inv['searched_but_not_in_sitemap'])} URLs get impressions but are missing from the sitemap.")
    if rival_reports:
        lines += ["", "## Rivals", ""]
        for r in rival_reports:
            if r.get("status") == "ok":
                lines.append(f"- **{r['domain']}**: {r['new']} new, {r['updated']} updated, {r['removed']} removed pages ({r['listed']} listed).")
                for p in r.get("pages", [])[:5]:
                    lines.append(f"  - {p.get('change')}: {p.get('title') or p['url']} ({p.get('words', '?')} words)")
            elif r.get("status") == "first-look":
                lines.append(f"- **{r['domain']}**: first look, {r['listed']} pages listed from its {r.get('list_from', 'sitemap')}. Changes are reported from next week.")
            elif r.get("status") == "blocked-by-robots":
                lines.append(f"- **{r['domain']}**: its robots.txt does not allow reading the sitemap, so it is not watched. Pick another rival?")
            else:
                lines.append(f"- **{r['domain']}**: {r.get('status')} ({'; '.join(r.get('errors', [])[:2])})")
    if judged:
        lines += ["", "## Past changes judged this week", ""]
        lines += [f"- {j['url']} ({j['kind']}, {j['days']} days): {j['verdict']}" for j in judged]
    failed = steps.failed()
    lines += ["", f"Steps: {len(steps.done) - len(failed)} ok" + (f", failed: {', '.join(failed)}" if failed else "") + "."]
    return "\n".join(lines) + "\n"


def run(site_dir: Path, ops_root: Path, as_of: date | None = None, raw_dir: Path | None = None, skip: set[str] | None = None, session=None) -> int:
    site = load_site(site_dir)
    skip = skip or set()
    started = runs.now()
    steps = Steps()
    week = iso_week(as_of or started.date())
    out = site.folder / "weeks" / week
    raw = Path(raw_dir) if raw_dir else Path(tempfile.mkdtemp(prefix=f"sitewell-{site.name}-"))

    if session is None:
        session = steps.run("sign in to Google", lambda: _signed_in_session())
    an = facts = inv = None
    meta = None
    end = None
    if session is not None:
        end = steps.run("find the latest complete day", lambda: gsc.latest_complete_day(session, site.property))
    if end is not None:
        meta = steps.run("fetch search console", lambda: gsc.fetch_bundle(session, site.property, raw / "gsc", end))
    dropped: dict[str, Any] = {}
    live, live_error = updates.fetch()
    if meta is not None:
        dropped = analysis.prepare(raw / "gsc", raw / "work", site)
        an = steps.run("detectors", lambda: analysis.run_detectors(raw / "work", site, live))
    if an is not None:
        facts = steps.run("facts", lambda: analysis.build_facts(an, site, meta, dropped, live_error))
        steps.run("history", lambda: update_history(site.folder / "history" / "monthly.csv", analysis.monthly(an, dropped.get("by_day"))))

    if "inventory" not in skip:
        numbers = inventory.page_numbers(an, end) if an is not None and end else {}
        own = Fetcher(own_hosts={site.domain})
        inv = steps.run("page inventory", lambda: inventory.build(site, own, numbers))

    rival_reports: list[dict[str, Any]] = []
    if "rivals" not in skip and site.rivals:
        fetcher = Fetcher(delay=1.5)
        for domain in site.rivals:
            snap_path = site.folder / "state" / "rivals" / f"{domain}.json"

            def scan_one(domain=domain, snap_path=snap_path):
                snapshot, report = rivals.scan(fetcher, domain, rivals.load_snapshot(snap_path))
                rivals.save_snapshot(snapshot, snap_path)
                return report

            report = steps.run(f"rival {domain}", scan_one)
            rival_reports.append(report or {"domain": domain, "status": "failed"})

    judged: list[dict[str, Any]] = []
    if an is not None:
        ledger_path = site.folder / "ledger.jsonl"

        def judge():
            changes = ledger.load(ledger_path)
            added = ledger.judge_all(changes, an, end)
            if added:
                ledger.save(changes, ledger_path)
            return added

        judged = steps.run("judge past changes", judge) or []

    out.mkdir(parents=True, exist_ok=True)
    doc = {
        "sitewell": __version__,
        "week": week,
        "generated": runs.now().isoformat(timespec="seconds"),
        "search_console": facts,
        "inventory": {k: v for k, v in inv.items() if k != "pages"} if inv else None,
        "rivals": rival_reports,
        "judged_changes": judged,
    }
    (out / "facts.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    if inv:
        inventory.write_csv(inv["pages"], out / "inventory.csv")
    (out / "summary.md").write_text(summary_md(site, facts, inv, rival_reports, judged, steps), encoding="utf-8")

    failed = set(steps.failed())
    status = "failed" if failed & STEPS_THAT_MATTER or meta is None else ("partial" if failed else "ok")
    runs.record(ops_root, site.name, "weekly", started, status, steps.done,
                {"week": week, "data_end": end.isoformat() if end else None, "sitewell": __version__})
    return 1 if status == "failed" else 0


def _signed_in_session():
    from google.auth.transport.requests import Request

    session = gsc.open_session()
    session.credentials.refresh(Request())
    return session
