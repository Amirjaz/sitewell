"""Phase 2: decide and write the week's actions, in shadow. Nothing is shipped.

1. Code settles locks, budget, evidence and pause (guardrails.before).
2. Claude decides (strong model, read-only workspace, schema-bound).
3. Code drops what the rules don't allow (guardrails.after_decision).
4. Code fetches the current content of every page involved.
5. Claude writes the drafts (strong model).
6. Code checks the drafts; the smallest model checks claims, language and overlap.
7. Each action ends as auto, needs approval, blocked, held or dropped, with reasons.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import tempfile
from datetime import date, datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any

from . import guardrails, ledger, llm, runs, schemas
from .config import Site, load_site
from .web import Fetcher, extract_content, normalise_url

HISTORY_WEEKS = 4


def prompt(name: str, **values: str) -> str:
    text = resources.files("sitewell.prompts").joinpath(f"{name}.md").read_text(encoding="utf-8")
    for k, v in values.items():
        text = text.replace("{" + k + "}", v)
    return text


def latest_week(site: Site) -> Path | None:
    weeks = sorted((site.folder / "weeks").glob("*/facts.json"))
    return weeks[-1].parent if weeks else None


def read_inventory(week_dir: Path) -> list[dict[str, Any]]:
    path = week_dir / "inventory.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def previous_proposals(site: Site, current: str) -> list[dict[str, Any]]:
    out = []
    for path in sorted((site.folder / "weeks").glob("*/shadow/decision.json"))[-HISTORY_WEEKS - 1 :]:
        week = path.parent.parent.name
        if week == current:
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        out.append({"week": week, "actions": [{k: a.get(k) for k in ("type", "url", "query", "final", "why")} for a in doc.get("actions", [])]})
    return out


def last_inventory_health(site: Site, current: str) -> dict[str, Any] | None:
    for path in sorted((site.folder / "weeks").glob("*/shadow/guard.json"), reverse=True):
        if path.parent.parent.name != current:
            return {"broken": json.loads(path.read_text(encoding="utf-8")).get("broken_pages", 0)}
    return None


def slug(url: str) -> str:
    s = re.sub(r"^https?://[^/]+", "", url).strip("/") or "home"
    return re.sub(r"[^\w\-]+", "-", s, flags=re.UNICODE)[:80].strip("-") or "page"


def fetch_packs(site: Site, urls: list[str], folder: Path, fetcher: Fetcher | None = None) -> dict[str, dict[str, Any]]:
    fetcher = fetcher or Fetcher(own_hosts={site.domain})
    folder.mkdir(parents=True, exist_ok=True)
    packs, index = {}, {}
    for url in dict.fromkeys(urls):
        got = fetcher.get(url)
        if not (got.ok and "html" in got.content_type):
            continue
        pack = extract_content(got.text, got.final_url or url)
        name = f"{slug(url)}.json"
        (folder / name).write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
        packs[normalise_url(url)] = pack
        index[normalise_url(url)] = name
    (folder / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return packs


def _copy(src: Path, dst: Path) -> None:
    if src.exists():
        shutil.copyfile(src, dst)


def run(site_dir: Path, ops_root: Path, today: date | None = None, runner: llm.Runner | None = None, fetcher: Fetcher | None = None) -> int:
    site = load_site(site_dir)
    today = today or datetime.now(timezone.utc).date()
    started = runs.now()
    calls: list[llm.Call] = []
    steps: list[dict[str, Any]] = []
    week_dir = latest_week(site)
    if week_dir is None:
        runs.record(ops_root, site.name, "shadow", started, "failed", [{"name": "find facts", "status": "failed", "error": "no weekly facts yet"}])
        return 1
    week = week_dir.name
    out = week_dir / "shadow"
    if out.exists():
        shutil.rmtree(out)
    (out / "drafts").mkdir(parents=True)
    facts = json.loads((week_dir / "facts.json").read_text(encoding="utf-8"))
    inv_rows = read_inventory(week_dir)
    inv_urls = {r["url"] for r in inv_rows}
    changes = ledger.load(site.folder / "ledger.jsonl")
    rules = site.folder / "rules.md"

    guard = guardrails.before(site, facts, changes, today, inv_rows, last_inventory_health(site, week))
    (out / "guard.json").write_text(json.dumps(guard, ensure_ascii=False, indent=1), encoding="utf-8")

    ws = Path(tempfile.mkdtemp(prefix=f"sitewell-shadow-{site.name}-"))
    _copy(rules, ws / "rules.md")
    if not (ws / "rules.md").exists():
        (ws / "rules.md").write_text("# Rules\n\nNo site rules written yet: follow the general principles only.\n", encoding="utf-8")
    shutil.copyfile(week_dir / "facts.json", ws / "facts.json")
    _copy(week_dir / "inventory.csv", ws / "inventory.csv")
    (ws / "guard.json").write_text(json.dumps(guard, ensure_ascii=False, indent=1), encoding="utf-8")
    (ws / "ledger.json").write_text(json.dumps([c.__dict__ for c in changes[-40:]], ensure_ascii=False, indent=1), encoding="utf-8")
    (ws / "previous.json").write_text(json.dumps(previous_proposals(site, week), ensure_ascii=False, indent=1), encoding="utf-8")

    system = prompt("system")
    decide = llm.run("decide", prompt("decide", domain=site.domain), ws, system, schemas.DECISION, runner=runner,
                     validate=lambda d: schemas.validate(d, schemas.DECISION))
    calls.append(decide)
    steps.append({"name": "decide", "status": "ok" if decide.ok else "failed", "error": decide.error})
    if not decide.ok:
        return _finish(site, ops_root, started, out, week, guard, None, [], {}, {}, calls, steps, facts)
    decision = decide.output
    actions = guardrails.after_decision(decision, guard, inv_urls)

    live = [a for a in actions if a["status"] != "dropped"]
    targets = [a["url"] for a in live if a["type"] not in ("new-page", "hand-task")]
    drafts: list[dict[str, Any]] = []
    packs: dict[str, dict[str, Any]] = {}
    code_checks: dict[str, dict[str, Any]] = {}
    model_checks: dict[str, dict[str, Any]] = {}
    if live:
        packs = fetch_packs(site, targets, ws / "pages", fetcher)
        steps.append({"name": "page packs", "status": "ok", "pages": len(packs)})
        (ws / "decision.json").write_text(json.dumps({"actions": live}, ensure_ascii=False, indent=1), encoding="utf-8")
        produce = llm.run("produce", prompt("produce", domain=site.domain), ws, system, schemas.DRAFTS, runner=runner, max_turns=20,
                          validate=lambda d: schemas.validate(d, schemas.DRAFTS))
        calls.append(produce)
        steps.append({"name": "produce", "status": "ok" if produce.ok else "failed", "error": produce.error})
        if produce.ok:
            drafts = produce.output["drafts"]
            code_checks = guardrails.after_drafts(live, drafts, packs, inv_urls, site.forbidden_phrases, site.approval_phrases)
            (ws / "drafts.json").write_text(json.dumps({"drafts": drafts}, ensure_ascii=False, indent=1), encoding="utf-8")
            check = llm.run("check", prompt("check"), ws, system, schemas.CHECKS, runner=runner,
                            validate=lambda d: schemas.validate(d, schemas.CHECKS))
            calls.append(check)
            steps.append({"name": "check", "status": "ok" if check.ok else "failed", "error": check.error})
            if check.ok:
                model_checks = {c["action_id"]: c for c in check.output["checks"]}
    return _finish(site, ops_root, started, out, week, guard, decision, actions, {d["action_id"]: d for d in drafts},
                   {"code": code_checks, "model": model_checks}, calls, steps, facts)


def _finish(site, ops_root, started, out, week, guard, decision, actions, drafts, checks, calls, steps, facts) -> int:
    for a in actions:
        status, reasons = guardrails.final_status(a, checks.get("code", {}).get(a["id"]), checks.get("model", {}).get(a["id"]), a["id"] in drafts)
        a["final"], a["final_reasons"] = status, reasons
        if a["id"] in drafts:
            a["draft"] = drafts[a["id"]]
            a["code_check"] = checks["code"].get(a["id"])
            a["model_check"] = checks["model"].get(a["id"])
            art = drafts[a["id"]].get("article") or {}
            if art.get("body_markdown"):
                (out / "drafts" / f"{a['id']}-{slug(art.get('slug') or a['url'])}.md").write_text(
                    f"# {art.get('h1') or art.get('title')}\n\n{art['body_markdown']}\n", encoding="utf-8")
    usage = {
        "calls": [c.record() for c in calls],
        "cost_usd_at_api_prices": round(sum(c.cost_usd or 0 for c in calls), 4),
        "tokens": {k: sum(c.tokens.get(k, 0) for c in calls) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")},
    }
    doc = {
        "week": week,
        "generated": runs.now().isoformat(timespec="seconds"),
        "mode": "shadow",
        "guard": guard,
        "week_summary": (decision or {}).get("week_summary"),
        "pause": (decision or {}).get("pause"),
        "actions": actions,
        "not_doing": (decision or {}).get("not_doing", []),
        "plan_revision": (decision or {}).get("plan_revision", ""),
        "usage": usage,
    }
    (out / "decision.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "report.md").write_text(report_md(site, doc), encoding="utf-8")
    failed = [s for s in steps if s["status"] == "failed"]
    status = "failed" if decision is None else ("partial" if failed else "ok")
    runs.record(ops_root, site.name, "shadow", started, status, steps, {"week": week, "usage": usage})
    return 1 if status == "failed" else 0


def _clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


STATUS_LABEL = {"auto": "would go live", "needs approval": "waits for you", "blocked": "blocked", "held": "held", "dropped": "dropped"}


def report_md(site: Site, doc: dict[str, Any]) -> str:
    g = doc["guard"]
    L = [f"## {site.domain}: shadow week {doc['week']}", ""]
    if doc.get("week_summary"):
        L += [doc["week_summary"], ""]
    dom = g.get("domain") or {}
    if dom.get("status") in ("renew soon", "expired", "on hold"):
        L += [f"**Domain {dom['domain']}: {dom['status']}**, expires {dom.get('expires')} ({dom.get('days_left')} days). Renew it.", ""]
    if g["paused"]:
        L += ["**Changes are paused:** " + "; ".join(g["pause_reasons"]) + ". The actions below are what sitewell would do once the pause ends.", ""]
    acts = doc["actions"]
    if not acts and doc.get("week_summary") is not None:
        L += ["No actions this week.", ""]
    if acts:
        L += ["| # | Action | Page | Outcome | Expected |", "|---|---|---|---|---|"]
        for a in acts:
            page = re.sub(r"^https?://[^/]+", "", a["url"]) or "/"
            L.append(f"| {a['id']} | {a['type']} | {page} | {STATUS_LABEL.get(a['final'], a['final'])} | {a['expected'][:80]} |")
        L.append("")
    for a in acts:
        L += [f"### {a['id']}: {a['type']} on {a['url']}", ""]
        if a.get("query"):
            L.append(f"Query: «{a['query']}»")
        L += [f"Why: {_clip(a['why'], 400)}", "", "Evidence: " + _clip("; ".join(a.get("evidence", [])), 400), "", f"Outcome: **{STATUS_LABEL.get(a['final'], a['final'])}**" + (f" ({_clip('; '.join(a['final_reasons']), 400)})" if a.get("final_reasons") else ""), ""]
        d = a.get("draft")
        if d:
            if d.get("summary"):
                L.append(f"- {d['summary']}")
            if d.get("new_title"):
                L.append(f"- New title: {d['new_title']}")
            if d.get("new_meta_description"):
                L.append(f"- New description: {d['new_meta_description']}")
            for link in d.get("links", []):
                L.append(f"- Link to {re.sub(r'^https?://[^/]+', '', link['to_url']) or '/'}, anchor «{link['anchor']}»")
            for e in d.get("text_edits", []):
                kind = "new block" if not e["before"] else "rewrite"
                L.append(f"- {kind.capitalize()} under «{e['section'][:60]}» ({len(e['after']):,} characters): {_clip(e['after'], 220)}")
            if d.get("schema_jsonld"):
                L.append("- Structured data: `" + d["schema_jsonld"][:200].replace("`", "'") + ("...`" if len(d["schema_jsonld"]) > 200 else "`"))
            art = d.get("article") or {}
            if art.get("body_markdown"):
                L.append(f"- Article draft «{art.get('title')}» ({len(art['body_markdown'].split())} words, {len(art.get('sources', []))} sources), in drafts/")
            ht = d.get("hand_task") or {}
            if ht.get("steps"):
                L.append(f"- For {ht.get('who')}, about {ht.get('minutes')} minutes: " + " ".join(f"({i + 1}) {s}" for i, s in enumerate(ht["steps"])))
            mc = a.get("model_check") or {}
            if guardrails.real(mc.get("language_issues")):
                L.append("- Language: " + "; ".join(_clip(x, 120) for x in guardrails.real(mc["language_issues"])[:3]))
            others = [o for o in mc.get("overlaps", []) if a["url"] not in o]
            if others:
                L.append("- Overlaps: " + "; ".join(_clip(x, 120) for x in others[:3]))
            cc = a.get("code_check") or {}
            if cc.get("warnings"):
                L.append("- Warnings: " + "; ".join(cc["warnings"]))
            if d.get("notes"):
                L.append(f"- Writer's note: {_clip(d['notes'], 300)}")
            L.append("")
    if doc.get("not_doing"):
        L += ["**Not doing:**", ""] + [f"- {n['what']}: {n['why']}" for n in doc["not_doing"]] + [""]
    if doc.get("plan_revision"):
        L += ["**Plan revision:** " + doc["plan_revision"], ""]
    u = doc["usage"]
    L.append(f"_{len(u['calls'])} model calls, ~{sum(u['tokens'].values()):,} tokens, ${u['cost_usd_at_api_prices']} at API prices._")
    return "\n".join(L) + "\n"
