"""The rules code enforces around the model, before and after it writes.

The model proposes; this module decides what may happen. Locks, budgets, pauses, phrase
checks and link checks are plain code, so they hold no matter what the model says.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from .ledger import Change
from .web import normalise_url

LOCK_DAYS = 14
NEW_PAGES_PER_30_DAYS = 2
MAX_ACTIONS = 5
MAX_HAND_TASKS = 3
TITLE_MAX, DESC_MIN, DESC_MAX = 65, 70, 170
LARGE_EDIT_CHARS = 1_200  # more new text than this on one page waits for a person

# Applied to new text only (a phrase already on the page is not the writer's doing).
FORBIDDEN = ["بهترین", "برترین", "تضمین", "صد درصد", "۱۰۰ درصد", "100 درصد", "۱۰۰٪", "100%", "معجزه"]
NEEDS_APPROVAL = ["قطعی", "درمان کامل", "قیمت", "هزینه", "تومان", "ریال", "عوارض ندارد", "بدون درد"]


def _norm(text: str) -> str:
    text = (text or "").replace("ي", "ی").replace("ك", "ک").replace("‌", " ")
    return re.sub(r"\s+", " ", text).strip()


def before(site, facts: dict[str, Any], changes: list[Change], today: date, inventory_rows: list[dict[str, Any]], last_inventory: dict[str, Any] | None) -> dict[str, Any]:
    """What is settled before the model decides: locks, budget, evidence, pause."""
    recent = [c for c in changes if (today - c.shipped_on).days < LOCK_DAYS]
    locked = sorted({c.url for c in recent})
    month = [c for c in changes if c.kind == "new-page" and (today - c.shipped_on).days < 30]
    sc = (facts or {}).get("search_console") or {}
    evidence = (sc.get("new_page_evidence") or {}).get("items", [])
    reasons = []
    for u in (sc.get("google_updates") or {}).get("ongoing", []):
        reasons.append(f"Google update rolling out: {u['name']} (since {u['start']})")
    broken = sum(1 for r in inventory_rows if str(r.get("status")) != "200" or str(r.get("noindex")) == "True")
    if last_inventory is not None and broken > last_inventory.get("broken", 0):
        reasons.append(f"pages failing or set to noindex went from {last_inventory['broken']} to {broken}")
    return {
        "today": today.isoformat(),
        "locked_pages": locked,
        "lock_reason": f"changed in the last {LOCK_DAYS} days",
        "new_page_budget": max(0, NEW_PAGES_PER_30_DAYS - len(month)),
        "new_page_evidence": [e["query"] for e in evidence],
        "paused": bool(reasons),
        "pause_reasons": reasons,
        "broken_pages": broken,
        "limits": {"max_actions": MAX_ACTIONS, "max_hand_tasks": MAX_HAND_TASKS},
    }


def after_decision(decision: dict[str, Any], guard: dict[str, Any], inventory_urls: set[str]) -> list[dict[str, Any]]:
    """Each proposed action with a status: proposed, dropped (and why), held."""
    out = []
    kept = hands = new_pages = 0
    locked = set(guard["locked_pages"])
    evidence = {_norm(q) for q in guard["new_page_evidence"]}
    for a in decision.get("actions", []):
        a = dict(a)
        url = normalise_url(a["url"]) if a.get("url") else ""
        a["url"] = url
        problems = []
        if a["type"] == "new-page":
            if _norm(a.get("query", "")) not in evidence:
                problems.append("no new-page evidence for this query")
            if new_pages >= guard["new_page_budget"]:
                problems.append("new-page budget for the month is used up")
        elif a["type"] != "hand-task":
            if url not in inventory_urls:
                problems.append("the page is not in the site's inventory")
            if url in locked:
                problems.append(f"page {guard['lock_reason']}")
        if a["type"] == "hand-task" and hands >= MAX_HAND_TASKS:
            problems.append(f"more than {MAX_HAND_TASKS} hand tasks")
        if not problems and kept >= MAX_ACTIONS:
            problems.append(f"more than {MAX_ACTIONS} actions")
        if problems:
            a["status"], a["reasons"] = "dropped", problems
        else:
            kept += 1
            hands += a["type"] == "hand-task"
            new_pages += a["type"] == "new-page"
            a["status"], a["reasons"] = ("held" if guard["paused"] else "proposed"), (list(guard["pause_reasons"]) if guard["paused"] else [])
        out.append(a)
    return out


def _new_phrases(new: str, old: str, phrases: list[str]) -> list[str]:
    n, o = _norm(new), _norm(old)
    return [p for p in phrases if _norm(p) in n and _norm(p) not in o]


def after_drafts(actions: list[dict[str, Any]], drafts: list[dict[str, Any]], packs: dict[str, dict[str, Any]], inventory_urls: set[str], site_forbidden: list[str], site_approval: list[str]) -> dict[str, dict[str, Any]]:
    """Code checks on every draft. Returns, per action id: problems (block), approvals (ask), warnings."""
    forbidden = FORBIDDEN + list(site_forbidden)
    approval = NEEDS_APPROVAL + list(site_approval)
    by_id = {a["id"]: a for a in actions}
    results: dict[str, dict[str, Any]] = {}
    for d in drafts:
        a = by_id.get(d["action_id"])
        r = {"problems": [], "approvals": [], "warnings": []}
        results[d["action_id"]] = r
        if a is None:
            r["problems"].append("draft for an action that was not proposed")
            continue
        pack = packs.get(a["url"]) or {}
        page_text = " ".join(b["text"] for b in pack.get("blocks", []))
        pairs = []  # (new text, the old text it replaces)
        if d.get("title"):
            pairs.append((d["title"], pack.get("title", "")))
            if len(d["title"]) > TITLE_MAX:
                r["warnings"].append(f"title is {len(d['title'])} characters")
        if d.get("description"):
            pairs.append((d["description"], pack.get("description", "")))
            if not DESC_MIN <= len(d["description"]) <= DESC_MAX:
                r["warnings"].append(f"description is {len(d['description'])} characters")
        added = 0
        for e in d.get("text_edits", []):
            old, new = _norm(e.get("before", "")), _norm(e.get("after", ""))
            if old and old not in _norm(page_text):
                r["problems"].append(f"'before' text not found on the page: {e['before'][:60]}")
            if old and old not in new:
                r["approvals"].append(f"rewrites existing text under «{e.get('section', '')[:40]}»")
            added += max(0, len(new) - len(old))
            pairs.append((e.get("after", ""), e.get("before", "") or page_text))
        if added > LARGE_EDIT_CHARS:
            r["approvals"].append(f"large edit: {added:,} characters of new text")
        for link in d.get("links", []):
            if normalise_url(link.get("to_url", "")) not in inventory_urls:
                r["problems"].append(f"link target is not a page on the site: {link.get('to_url')}")
            pairs.append((link.get("sentence", ""), page_text))
        if d.get("schema_jsonld"):
            try:
                json.loads(d["schema_jsonld"])
            except ValueError as exc:
                r["problems"].append(f"structured data is not valid JSON: {exc}")
        art = d.get("article") or {}
        if art.get("body_markdown"):
            pairs.append((" ".join([art.get("title", ""), art.get("description", ""), art["body_markdown"]]), ""))
            if not art.get("sources"):
                r["problems"].append("article cites no sources")
        for new, old in pairs:
            for p in _new_phrases(new, old, forbidden):
                r["problems"].append(f"uses a forbidden phrase: «{p}»")
            for p in _new_phrases(new, old, approval):
                r["approvals"].append(f"new text says «{p}»")
        r["problems"] = list(dict.fromkeys(r["problems"]))
        r["approvals"] = list(dict.fromkeys(r["approvals"]))
    return results


def final_status(action: dict[str, Any], code: dict[str, Any] | None, check: dict[str, Any] | None, has_draft: bool) -> tuple[str, list[str]]:
    """auto | needs approval | blocked | held | dropped, with the reasons."""
    if action["status"] == "dropped":
        return "dropped", action["reasons"]
    if not has_draft and action["type"] != "hand-task":
        return "blocked", ["no draft was written"]
    reasons: list[str] = []
    code = code or {"problems": [], "approvals": [], "warnings": []}
    check = check or {}
    if code["problems"] or check.get("rule_conflicts"):
        return "blocked", code["problems"] + [f"rule: {c}" for c in check.get("rule_conflicts", [])]
    if action["type"] in ("new-page", "technical"):
        reasons.append("new pages always wait for approval" if action["type"] == "new-page" else "code changes wait until the path check exists (Phase 4)")
    if action["type"] == "hand-task":
        reasons.append("a person has to do this")
    reasons += code["approvals"]
    reasons += [f"new medical claim: {c}" for c in check.get("new_medical_claims", [])]
    status = "needs approval" if reasons else "auto"
    if action["status"] == "held":
        return "held", action["reasons"] + ([f"then: {status}"] if status else [])
    return status, reasons
