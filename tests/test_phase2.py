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
    d = {"action_id": aid, "summary": "", "new_title": "", "new_meta_description": "", "links": [], "schema_jsonld": "", "text_edits": [],
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
        empty_draft("a1", new_title="درمان الف در تهران | نوبت دکتر نمونه"),
        empty_draft("a2", text_edits=[{"section": "علت", "before": "درمان الف برای بیماری ب انجام می‌شود.", "after": "درمان الف برای بیماری ب انجام می‌شود و دو هفته طول می‌کشد."}]),
        empty_draft("a3", article={"slug": "illness-c", "title": "علت بیماری ج", "description": "d", "h1": "علت بیماری ج", "body_markdown": "## علت\n\nمتن.", "sources": [{"title": "ref", "url": "https://ref.test"}]}),
        empty_draft("a6", new_meta_description="بهترین درمان الف در تهران با دکتر نمونه، همراه با توضیح کامل علت‌ها و راه‌های پیشگیری از بیماری"),
        empty_draft("a7", links=[{"from_url": f"{SITE}/a", "to_url": f"{SITE}/missing", "anchor": "x", "sentence": "x"}]),
    ]
}

CHECKS = {"checks": [
    {"action_id": "a1", "new_medical_claims": [], "language_issues": [], "overlaps": [], "breaks_never_rules": [], "needs_doctor": []},
    {"action_id": "a2", "new_medical_claims": ["دو هفته طول می‌کشد"], "language_issues": [], "overlaps": [], "breaks_never_rules": [], "needs_doctor": []},
    {"action_id": "a3", "new_medical_claims": [], "language_issues": [], "overlaps": [], "breaks_never_rules": [], "needs_doctor": []},
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
    assert final["a2"][0] == "needs approval" and any("new medical claim" in r for r in final["a2"][1])
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
    r = guardrails.after_drafts(acts, [empty_draft("a1", new_title="بهترین کلینیک تهران")], pack, {f"{SITE}/a"}, [], [])
    assert r["a1"]["problems"] == []  # the old title already said it
    r = guardrails.after_drafts(acts, [empty_draft("a1", new_title="هزینه درمان الف")], {f"{SITE}/a": {"title": "درمان الف", "blocks": []}}, {f"{SITE}/a"}, [], [])
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


def test_importing_old_changes_is_idempotent(tmp_path):
    src = tmp_path / "import.jsonl"
    rows = [
        {"id": "imp-1", "site": "clinic", "url": "https://clinic.test/%D8%A7/", "kind": "title", "shipped": "2026-11-01", "summary": "t", "before": "a", "after": "b", "ref": None, "source": "imported", "judged": {}},
        {"id": "imp-2", "site": "clinic", "url": "https://clinic.test/b", "kind": "banana", "shipped": "2026-11-02", "summary": "t", "before": None, "after": None, "ref": None, "source": "imported", "judged": {}},
    ]
    src.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    path = tmp_path / "ledger.jsonl"
    added, problems = ledger.import_file(path, src)
    assert added == 1 and problems == ["line 2: unknown kind 'banana'"]
    assert ledger.load(path)[0].url == "https://clinic.test/ا"
    assert ledger.import_file(path, src)[0] == 0


def test_rewording_existing_text_and_large_additions_wait_for_a_person():
    acts = [dict(action(1, "text-edit", "/a"), status="proposed", reasons=[])]
    pack = {f"{SITE}/a": {"title": "", "blocks": [{"tag": "p", "text": "درمان الف تنها درمان قطعی است."}]}}
    reword = empty_draft("a1", text_edits=[{"section": "علت", "before": "درمان الف تنها درمان قطعی است.", "after": "درمان الف یک درمان است."}])
    r = guardrails.after_drafts(acts, [reword], pack, {f"{SITE}/a"}, [], [])
    assert r["a1"]["approvals"] == ["rewrites existing text under «علت»"]
    extend = empty_draft("a1", text_edits=[{"section": "علت", "before": "درمان الف تنها درمان قطعی است.", "after": "درمان الف تنها درمان قطعی است. نوبت بگیرید."}])
    assert guardrails.after_drafts(acts, [extend], pack, {f"{SITE}/a"}, [], [])["a1"]["approvals"] == []
    big = empty_draft("a1", text_edits=[{"section": "s", "before": "", "after": "متن " * 400}])
    assert guardrails.after_drafts(acts, [big], pack, {f"{SITE}/a"}, [], [])["a1"]["approvals"][0].startswith("large edit")


def test_domain_expiry_from_rdap():
    from sitewell import health

    class R:
        def __init__(self, doc, code=200):
            self.doc, self.status_code = doc, code

        def json(self):
            return self.doc

    class S:
        def __init__(self, doc, code=200):
            self.r = R(doc, code)

        def get(self, url, timeout, headers):
            assert url == "https://rdap.org/domain/clinic.test"
            return self.r

    doc = {"status": ["client transfer prohibited"], "events": [{"eventAction": "expiration", "eventDate": "2026-12-13T13:31:27Z"}]}
    d = health.domain("www.clinic.test", date(2026, 11, 15), session=S(doc))
    assert (d["status"], d["days_left"], d["expires"]) == ("renew soon", 28, "2026-12-13")
    assert health.domain("clinic.test", date(2026, 1, 1), session=S(doc))["status"] == "ok"
    assert health.domain("clinic.test", date(2026, 11, 15), session=S({"status": ["client hold"], "events": doc["events"]}))["status"] == "on hold"
    assert health.domain("clinic.test", date(2026, 11, 15), session=S({}, 404))["status"] == "unknown"


def test_misplaced_fields_and_checker_noise():
    acts = [dict(action(1, "internal-link", "/a"), status="proposed", reasons=[])]
    pack = {f"{SITE}/a": {"title": "t", "description": "d", "blocks": []}}
    r = guardrails.after_drafts(acts, [empty_draft("a1", new_title="یک برچسب، نه عنوان")], pack, {f"{SITE}/a"}, [], [])
    assert r["a1"]["problems"] == ["a new title in a internal-link action"]
    acts = [dict(action(1, "description", "/a"), status="proposed", reasons=[])]
    r = guardrails.after_drafts(acts, [empty_draft("a1")], pack, {f"{SITE}/a"}, [], [])
    assert r["a1"]["problems"] == ["a description action with no new description"]
    check = {"breaks_never_rules": ["No breach found. The message is fine."], "needs_doctor": ["medical wording in the description"], "new_medical_claims": ["None"]}
    status, reasons = guardrails.final_status(dict(acts[0], status="proposed"), {"problems": [], "approvals": [], "warnings": []}, check, True)
    assert (status, reasons) == ("needs approval", ["needs the doctor: medical wording in the description"])


def test_snippets_empty_drafts_and_hand_tasks():
    pack = {f"{SITE}/a": {"title": "t", "description": "d", "blocks": []}}
    acts = [dict(action(1, "snippet", "/a"), status="held", reasons=["Google update"])]
    r = guardrails.after_drafts(acts, [empty_draft("a1", new_title="عنوان تازه", new_meta_description="توضیح " * 20)], pack, {f"{SITE}/a"}, [], [])
    assert r["a1"]["problems"] == []
    assert guardrails.after_drafts(acts, [empty_draft("a1")], pack, {f"{SITE}/a"}, [], [])["a1"]["problems"] == ["a snippet action with neither a new title nor a new description"]
    hand = dict(action(2, "hand-task", "", ""), status="held", reasons=["Google update"])
    assert guardrails.final_status(hand, None, None, True) == ("for you", ["a person has to do this"])
    nothing = dict(action(3, "internal-link", "/a"), status="held", reasons=["Google update"], empty_draft=True)
    assert guardrails.final_status(nothing, None, None, True)[0] == "no change needed"
    assert guardrails.empty(empty_draft("a3")) and not guardrails.empty(empty_draft("a3", new_title="x"))
    flagged = dict(action(4, "description", "/a"), status="proposed", reasons=[])
    status, reasons = guardrails.final_status(flagged, {"problems": [], "approvals": [], "warnings": []}, {"breaks_never_rules": ["claims a cause"]}, True)
    assert status == "needs approval" and reasons == ["the checker says it breaks a rule: claims a cause"]


def test_an_em_dash_in_new_text_is_blocked():
    acts = [dict(action(1, "title", "/a"), status="proposed", reasons=[])]
    pack = {f"{SITE}/a": {"title": "درمان الف | دکتر نمونه", "blocks": []}}
    r = guardrails.after_drafts(acts, [empty_draft("a1", new_title="درمان الف — دکتر نمونه")], pack, {f"{SITE}/a"}, [], [])
    assert r["a1"]["problems"] == ["uses a forbidden phrase: «—»"]
