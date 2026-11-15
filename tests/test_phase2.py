"""Phase 2 with a fake model and fake pages: every guardrail, no network, no Claude."""

import csv
import json
from datetime import date

import pytest

from sitewell import guardrails, ledger, llm, schemas, shadow
from sitewell.web import Fetched, extract_content

SITE = "https://clinic.test"
TODAY = date(2026, 11, 20)

PAGE = """<html><head><title>درمان الف در تهران | دکتر نمونه</title><meta name="description" content="توضیح کوتاه درباره درمان الف">
<script type="application/ld+json">{"@type":"MedicalWebPage"}</script></head><body><header><nav><a href="/">خانه</a></nav></header>
<main><h1>درمان الف</h1><h2>علت</h2><p>درمان الف برای بیماری ب انجام می‌شود.</p><p>برای نوبت تماس بگیرید.</p>
<a href="/b/">صفحه ب</a></main></body></html>"""


def make_site(tmp_path, rules=True, ongoing=False):
    folder = tmp_path / "sites" / "clinic"
    week = folder / "weeks" / "2026-W47"
    week.mkdir(parents=True)
    (folder / "site.toml").write_text(
        f'name = "clinic"\nkind = "wordpress"\nproperty = "{SITE}/"\nbase_url = "{SITE}"\nforbidden_phrases = ["ارزان‌ترین"]\n', encoding="utf-8"
    )
    if rules:
        (folder / "rules.md").write_text("# Rules: clinic.test\n\n## Never\n\n- No superlatives.\n", encoding="utf-8")
    facts = {
        "search_console": {
            "new_page_evidence": {"items": [{"query": "علت بیماری ج", "impressions": 80}]},
            "google_updates": {"ongoing": [{"name": "November 2026 core update", "start": "2026-11-10"}] if ongoing else []},
        }
    }
    (week / "facts.json").write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")
    with (week / "inventory.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["url", "status", "noindex", "title"])
        w.writeheader()
        for path in ("/a", "/b", "/locked"):
            w.writerow({"url": f"{SITE}{path}", "status": "200", "noindex": "False", "title": path})
    ledger.add(folder / "ledger.jsonl", ledger.Change(id="old1", site="clinic", url=f"{SITE}/locked", kind="title", shipped="2026-11-15", summary="x", source="imported"))
    return folder


class FakePages:
    def get(self, url, check_robots=True):
        return Fetched(url, 200, final_url=url, content_type="text/html", text=PAGE)


def action(i, type_, path, query="", **kw):
    return {"id": f"a{i}", "type": type_, "url": f"{SITE}{path}", "query": query, "evidence": ["striking_distance: q pos 9, 200 imp"],
            "why": "w", "expected": "200 x (0.10 - 0.02) = 16 clicks/month", "instruction": "i", "risk": "low", **kw}


def empty_draft(aid, **kw):
    d = {"action_id": aid, "title": "", "description": "", "links": [], "schema_jsonld": "", "text_edits": [],
         "article": {"slug": "", "title": "", "description": "", "h1": "", "body_markdown": "", "sources": []},
         "hand_task": {"who": "", "steps": [], "minutes": 0}, "notes": ""}
    d.update(kw)
    return d


DECISION = {
    "week_summary": "Clicks rose 20%.",
    "pause": {"recommended": False, "reasons": []},
    "actions": [
        action(1, "title", "/a", "درمان الف"),
        action(2, "text-edit", "/a", "علت بیماری ب"),
        action(3, "new-page", "/علت-بیماری-ج", "علت بیماری ج"),
        action(4, "title", "/locked"),
        action(5, "new-page", "/دیگر", "پرسش بی‌شاهد"),
        action(6, "description", "/a"),
        action(7, "internal-link", "/a"),
    ],
    "not_doing": [{"what": "x", "why": "y"}],
    "plan_revision": "",
}

DRAFTS = {
    "drafts": [
        empty_draft("a1", title="درمان الف در تهران | نوبت دکتر نمونه"),
        empty_draft("a2", text_edits=[{"section": "علت", "before": "درمان الف برای بیماری ب انجام می‌شود.", "after": "درمان الف برای بیماری ب انجام می‌شود و دو هفته طول می‌کشد."}]),
        empty_draft("a3", article={"slug": "illness-c", "title": "علت بیماری ج", "description": "d", "h1": "علت بیماری ج", "body_markdown": "## علت\n\nمتن.", "sources": [{"title": "ref", "url": "https://ref.test"}]}),
        empty_draft("a6", description="بهترین درمان الف در تهران با دکتر نمونه، همراه با توضیح کامل علت‌ها و راه‌های پیشگیری از بیماری"),
        empty_draft("a7", links=[{"from_url": f"{SITE}/a", "to_url": f"{SITE}/missing", "anchor": "x", "sentence": "x"}]),
    ]
}

CHECKS = {"checks": [
    {"action_id": "a1", "new_medical_claims": [], "language_issues": [], "overlaps": [], "rule_conflicts": []},
    {"action_id": "a2", "new_medical_claims": ["دو هفته طول می‌کشد"], "language_issues": [], "overlaps": [], "rule_conflicts": []},
    {"action_id": "a3", "new_medical_claims": [], "language_issues": [], "overlaps": [], "rule_conflicts": []},
]}


class FakeModel:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, step, prompt, workdir, system, schema, model, max_turns):
        self.calls.append((step, model, sorted(p.name for p in workdir.iterdir())))
        out = self.answers[step]
        if callable(out):
            out = out(len([c for c in self.calls if c[0] == step]))
        return llm.Call(step, model, ok=out is not None, output=out, cost_usd=0.01, tokens={"input_tokens": 10, "output_tokens": 5})


def test_the_shadow_week_sorts_every_action(tmp_path):
    folder = make_site(tmp_path)
    model = FakeModel({"decide": DECISION, "produce": DRAFTS, "check": CHECKS})
    assert shadow.run(folder, tmp_path, today=TODAY, runner=model, fetcher=FakePages()) == 0
    doc = json.loads((folder / "weeks" / "2026-W47" / "shadow" / "decision.json").read_text(encoding="utf-8"))
    final = {a["id"]: (a["final"], a["final_reasons"]) for a in doc["actions"]}
    assert final["a1"] == ("auto", [])
    assert final["a2"][0] == "needs approval" and "new medical claim" in final["a2"][1][0]
    assert final["a3"] == ("needs approval", ["new pages always wait for approval"])
    assert final["a4"][0] == "dropped" and "changed in the last 14 days" in final["a4"][1][0]
    assert final["a5"] == ("dropped", ["no new-page evidence for this query"])
    assert final["a6"][0] == "blocked" and "«بهترین»" in final["a6"][1][0]
    assert final["a7"][0] == "blocked" and "not a page on the site" in final["a7"][1][0]
    # The model only ever saw its workspace, with the site's rules in it.
    decide_files = model.calls[0][2]
    assert {"rules.md", "facts.json", "guard.json", "inventory.csv", "ledger.json", "previous.json"} <= set(decide_files)
    assert [c[1] for c in model.calls] == ["claude-opus-5-5", "claude-opus-5-5", "claude-haiku-5-5"]
    report = (folder / "weeks" / "2026-W47" / "shadow" / "report.md").read_text(encoding="utf-8")
    assert "would go live" in report and "waits for you" in report and "3 model calls" in report
    assert list((folder / "weeks" / "2026-W47" / "shadow" / "drafts").glob("a3-*.md"))


def test_a_rolling_google_update_holds_everything(tmp_path):
    folder = make_site(tmp_path, ongoing=True)
    model = FakeModel({"decide": DECISION, "produce": DRAFTS, "check": CHECKS})
    shadow.run(folder, tmp_path, today=TODAY, runner=model, fetcher=FakePages())
    doc = json.loads((folder / "weeks" / "2026-W47" / "shadow" / "decision.json").read_text(encoding="utf-8"))
    a1 = next(a for a in doc["actions"] if a["id"] == "a1")
    assert a1["final"] == "held" and "November 2026 core update" in a1["final_reasons"][0] and a1["final_reasons"][-1] == "then: auto"
    assert doc["guard"]["paused"] is True


def test_a_bad_answer_is_retried_once_then_reported(tmp_path):
    folder = make_site(tmp_path)
    broken = dict(DECISION, actions=[{"id": "a1"}])
    model = FakeModel({"decide": lambda n: broken if n == 1 else DECISION, "produce": DRAFTS, "check": CHECKS})
    assert shadow.run(folder, tmp_path, today=TODAY, runner=model, fetcher=FakePages()) == 0
    assert [c[0] for c in model.calls].count("decide") == 2

    model = FakeModel({"decide": broken, "produce": DRAFTS, "check": CHECKS})
    assert shadow.run(folder, tmp_path, today=TODAY, runner=model, fetcher=FakePages()) == 1
    record = sorted((tmp_path / "runs" / "clinic").glob("*-shadow.json"))[-1]
    assert json.loads(record.read_text(encoding="utf-8"))["status"] == "failed"


def test_budget_and_limits():
    guard = {"locked_pages": [], "lock_reason": "", "new_page_budget": 1, "new_page_evidence": ["الف", "ب"], "paused": False, "pause_reasons": []}
    acts = guardrails.after_decision(
        {"actions": [action(1, "new-page", "/x", "الف"), action(2, "new-page", "/y", "ب")] + [action(i, "hand-task", "", "") for i in range(3, 8)]},
        guard, set())
    status = [a["status"] for a in acts]
    assert status[:2] == ["proposed", "dropped"] and acts[1]["reasons"] == ["new-page budget for the month is used up"]
    assert status[2:] == ["proposed", "proposed", "proposed", "dropped", "dropped"]


def test_phrases_count_only_when_the_writer_added_them():
    acts = [dict(action(1, "title", "/a"), status="proposed", reasons=[])]
    pack = {f"{SITE}/a": {"title": "بهترین کلینیک", "description": "", "blocks": []}}
    r = guardrails.after_drafts(acts, [empty_draft("a1", title="بهترین کلینیک تهران")], pack, {f"{SITE}/a"}, [], [])
    assert r["a1"]["problems"] == []  # the old title already said it
    r = guardrails.after_drafts(acts, [empty_draft("a1", title="هزینه درمان الف")], {f"{SITE}/a": {"title": "درمان الف", "blocks": []}}, {f"{SITE}/a"}, [], [])
    assert r["a1"]["approvals"] == ["new text says «هزینه»"]


def test_extract_content_keeps_order_links_and_structured_data():
    pack = extract_content(PAGE, f"{SITE}/a")
    assert [b["tag"] for b in pack["blocks"]] == ["h1", "h2", "p", "p"]
    assert pack["internal_links"] == [{"anchor": "صفحه ب", "href": f"{SITE}/b"}]
    assert pack["jsonld"] == ['{"@type":"MedicalWebPage"}'] and pack["description"].startswith("توضیح")


def test_the_schemas_accept_good_answers_and_name_what_is_wrong():
    assert schemas.validate(DECISION, schemas.DECISION) == []
    assert schemas.validate(DRAFTS, schemas.DRAFTS) == []
    assert schemas.validate(CHECKS, schemas.CHECKS) == []
    bad = schemas.validate({"week_summary": "x"}, schemas.DECISION)
    assert any("'pause' is a required property" in b for b in bad)
