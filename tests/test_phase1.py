"""Phase 1 on synthetic data: no network, no Google, every number known in advance."""

import csv
import json
import math
from datetime import date, datetime, timedelta, timezone

import pytest

from sitewell import analysis, inventory, ledger, rivals, runs, week
from sitewell.config import ConfigError, load_site
from sitewell.web import Fetched, normalise_url, parse_page, parse_sitemap, read_sitemaps

END = date(2026, 11, 8)  # a Sunday, so the weeks come out whole
DAYS = 140
SITE = "https://example-clinic.test"
CHANGED = f"{SITE}/treatment-a/"
SHIPPED = END - timedelta(days=20)


def write(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def site_dir(tmp_path):
    folder = tmp_path / "sites" / "clinic"
    folder.mkdir(parents=True)
    (folder / "site.toml").write_text(
        f'''name = "clinic"
kind = "wordpress"
property = "{SITE}/"
base_url = "{SITE}"
brand_terms = ["دکتر نمونه"]
ignore_query_patterns = ["در جدول$"]
rivals = ["rival.test"]
''',
        encoding="utf-8",
    )
    return folder


@pytest.fixture
def bundle(tmp_path):
    """140 days. Steady ~40 clicks a day, a 3-day bot burst, one page that doubles after a change."""
    raw = tmp_path / "raw"
    raw.mkdir()
    start = END - timedelta(days=DAYS - 1)
    burst = {END - timedelta(days=60 + i) for i in range(3)}
    dates, queries, pages, countries, devices, qp = [], [], [], [], [], []
    for i in range(DAYS):
        d = start + timedelta(days=i)
        weekday = 1.0 + 0.15 * math.sin(i * 2 * math.pi / 7)
        brand, generic, weak, crossword = round(10 * weekday), round(22 * weekday), 0, 3
        changed_clicks = 8 if d > SHIPPED else 4
        bot = 120 if d in burst else 0
        total = brand + generic + changed_clicks + crossword + bot + 2  # 2 anonymised
        imp = 1500 + (900 if d in burst else 0)
        dates.append({"date": d.isoformat(), "clicks": total, "impressions": imp, "ctr": total / imp, "position": 6.0})
        rows = [
            ("دکتر نمونه", brand, 60, 1.1, f"{SITE}/"),
            ("درمان الف", generic + bot, 700, 4.0, f"{SITE}/treatment-b/"),
            ("هزینه درمان الف", changed_clicks, 300, 5.0, CHANGED),
            ("علت بیماری ب", weak, 12, 24.0, f"{SITE}/"),
            ("بیماری دهان در جدول", crossword, 40, 3.0, f"{SITE}/glossary/"),
        ]
        for q, c, im, pos, page in rows:
            queries.append({"date": d.isoformat(), "query": q, "clicks": c, "impressions": im, "ctr": c / im, "position": pos})
            if (END - d).days < 56:
                qp.append({"date": d.isoformat(), "query": q, "page": page, "clicks": c, "impressions": im, "ctr": c / im, "position": pos})
        for page, c, im in ((f"{SITE}/", brand + 2, 300), (f"{SITE}/treatment-b/", generic + bot, 700), (CHANGED, changed_clicks, 300)):
            pages.append({"date": d.isoformat(), "page": page, "clicks": c, "impressions": im, "ctr": c / im, "position": 4.0})
        sg = bot if d in burst else 0
        countries += [
            {"date": d.isoformat(), "country": "irn", "clicks": total - sg, "impressions": imp - (900 if sg else 0), "ctr": 0, "position": 6},
            {"date": d.isoformat(), "country": "sgp", "clicks": sg, "impressions": 900 if sg else 0, "ctr": 0, "position": 6},
        ]
        devices += [{"date": d.isoformat(), "device": "MOBILE", "clicks": total, "impressions": imp, "ctr": 0, "position": 6}]
    m = ["clicks", "impressions", "ctr", "position"]
    write(raw / "Dates.csv", ["date", *m], dates)
    write(raw / "queries.csv", ["date", "query", *m], queries)
    write(raw / "pages.csv", ["date", "page", *m], pages)
    write(raw / "countries.csv", ["date", "country", *m], countries)
    write(raw / "devices.csv", ["date", "device", *m], devices)
    write(raw / "query_pages.csv", ["date", "query", "page", *m], qp)
    meta = {"site": f"{SITE}/", "start": start.isoformat(), "end": END.isoformat(), "rows": {"Dates": len(dates)}}
    return raw, meta


@pytest.fixture
def analysed(bundle, site_dir, tmp_path):
    raw, meta = bundle
    site = load_site(site_dir)
    dropped = analysis.prepare(raw, tmp_path / "work", site)
    an = analysis.run_detectors(tmp_path / "work", site)
    return site, an, meta, dropped


# ------------------------------------------------------------------ analysis ---


def test_ignored_queries_are_dropped_before_anything_is_measured(analysed):
    site, an, meta, dropped = analysed
    assert dropped["rows"] == DAYS and dropped["clicks"] == 3 * DAYS
    facts = analysis.build_facts(an, site, meta, dropped)
    assert any("ignore_query_patterns" in n for n in facts["notes"])


def test_the_bot_burst_is_found_and_kept_out_of_the_baseline(analysed):
    site, an, meta, dropped = analysed
    facts = analysis.build_facts(an, site, meta, dropped)
    assert len(facts["bot_bursts"]) == 1
    b = facts["bot_bursts"][0]
    assert b["excess"] > 300
    assert any(c["value"].lower() == "sgp" for c in b["concentrated_in"])
    weeks = facts["baseline"]["weeks"]
    assert all(len(w["week"]) == 8 for w in weeks) and len(weeks) == DAYS // 7
    burst_week = next(w for w in weeks if w["bot_excess"] > 0)
    assert burst_week["bot_excess"] > 300
    # Non-brand excludes the brand query, the crossword clicks and the bot excess.
    normal = weeks[-1]
    assert 200 < normal["nonbrand"] < 280 and normal["brand"] > 60


def test_kpis_and_json_are_plain(analysed):
    site, an, meta, dropped = analysed
    facts = analysis.build_facts(an, site, meta, dropped)
    assert facts["kpis"]["current"]["clicks"] > 0
    assert facts["periods"]["current"]["end"] == END.isoformat()
    json.dumps(facts)  # nothing pandas or numpy left in it
    assert 0 < facts["brand_click_share"] < 1


def test_a_query_no_page_answers_is_evidence_for_a_new_page(analysed):
    site, an, meta, dropped = analysed
    ev = analysis.build_facts(an, site, meta, dropped)["new_page_evidence"]
    queries = {r["query"]: r for r in ev["items"]}
    assert "علت بیماری ب" in queries
    assert "best page ranks 24.0" in queries["علت بیماری ب"]["why"]
    assert "دکتر نمونه" not in queries  # brand queries never count
    assert "درمان الف" not in queries  # answered well already


def test_monthly_history_marks_cut_off_months(analysed, tmp_path):
    site, an, meta, dropped = analysed
    months = analysis.monthly(an)
    assert months[0]["complete"] is False and months[-2]["complete"] is True
    path = tmp_path / "monthly.csv"
    week.update_history(path, months)
    full = {m["month"]: m for m in months}
    # A later run whose window starts mid-month must not overwrite the complete count.
    later = [dict(full[months[1]["month"]], complete=False, clicks=1)]
    week.update_history(path, later)
    rows = {r["month"]: r for r in csv.DictReader(path.open(encoding="utf-8"))}
    assert rows[months[1]["month"]]["clicks"] == str(full[months[1]["month"]]["clicks"])


# -------------------------------------------------------------------- ledger ---


def test_a_page_that_doubled_after_a_change_is_judged_a_gain(analysed, tmp_path):
    site, an, meta, dropped = analysed
    path = tmp_path / "ledger.jsonl"
    ledger.add(path, ledger.Change(id="c1", site="clinic", url=CHANGED, kind="title", shipped=SHIPPED.isoformat(), summary="new title"))
    changes = ledger.load(path)
    added = ledger.judge_all(changes, an, END)
    assert [a["days"] for a in added] == [14]  # 28 days have not passed yet
    j = changes[0].judged["14"]
    assert j["verdict"] == "gain" and j["page"]["clicks"] == [56, 112]
    ledger.save(changes, path)
    assert ledger.judge_all(ledger.load(path), an, END) == []  # judged once, not again
    with pytest.raises(ValueError, match="already in the ledger"):
        ledger.add(path, ledger.Change(id="c1", site="clinic", url=CHANGED, kind="title", shipped="2026-01-01", summary="x"))


# ---------------------------------------------------------------------- runs ---


def test_missing_and_stale_runs_are_reported(tmp_path):
    at = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
    assert runs.check(tmp_path, ["clinic"], at=at) == ["clinic: no successful weekly run recorded"]
    runs.record(tmp_path, "clinic", "weekly", at - timedelta(days=2), "ok", [])
    assert runs.check(tmp_path, ["clinic"], at=at) == []
    assert "days ago" in runs.check(tmp_path, ["clinic"], at=at + timedelta(days=9))[0]
    runs.record(tmp_path, "clinic", "weekly", at - timedelta(days=1), "failed", [{"name": "detectors", "status": "failed"}])
    assert runs.check(tmp_path, ["clinic"], at=at) == ["clinic: the latest weekly run failed (detectors)"]


# ----------------------------------------------------------------------- web ---

PAGE = """<!doctype html><html lang="fa" dir="rtl"><head><title>درمان الف | کلینیک</title>
<meta name="description" content="توضیح"><link rel="canonical" href="/treatment-a/">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Article","dateModified":"2026-10-01"},{"@type":"FAQPage"}]}</script>
</head><body><nav>منو منو منو</nav><main><h1>درمان الف</h1><h2>علت</h2><p>این یک متن کوتاه درباره درمان است</p>
<a href="/b/">ب</a><a href="https://other.test/">x</a><img src="a.jpg"><img src="b.jpg" alt="تصویر"></main><footer>پایین</footer></body></html>"""


def test_parse_page_reads_what_a_review_needs():
    info = parse_page(PAGE, CHANGED)
    assert info["title"] == "درمان الف | کلینیک" and info["lang"] == "fa" and info["dir"] == "rtl"
    assert info["canonical"] == CHANGED and info["h1"] == ["درمان الف"] and info["h2_count"] == 1
    assert info["schema_types"] == ["Article", "FAQPage"] and info["modified"] == "2026-10-01"
    assert info["internal_links"] == 1 and info["external_links"] == 1 and info["images_without_alt"] == 1
    assert info["words"] == 10  # nav and footer don't count


def test_nested_page_chrome_is_removed_without_errors():
    html = ('<html><body><header><nav><a href="/"><img src="logo.png">خانه</a></nav></header><svg><script>x()</script></svg>'
            '<main><h1>عنوان</h1><p>سه کلمه اینجا</p><img src="a.jpg" alt="الف"></main><footer><nav>پیوند</nav></footer></body></html>')
    info = parse_page(html, "https://a.test/")
    assert info["h1"] == ["عنوان"] and info["words"] == 4
    assert info["images"] == 2 and info["images_without_alt"] == 1


def test_sitemap_parsing_and_url_normalising():
    kind, rows = parse_sitemap('<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://a.test/post-sitemap.xml</loc></sitemap></sitemapindex>')
    assert kind == "index" and rows == [("https://a.test/post-sitemap.xml", None)]
    with pytest.raises(ValueError):
        parse_sitemap("<html></html>")
    assert normalise_url("https://A.test/%D8%AF%D8%B1%D9%85%D8%A7%D9%86/#x") == "https://a.test/درمان"
    assert normalise_url("https://a.test") == "https://a.test/"


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def get(self, url, check_robots=True):
        self.asked.append(url)
        body = self.pages.get(url)
        if body is None:
            return Fetched(url, 404)
        ctype = "application/xml" if url.endswith(".xml") or url.endswith(".txt") else "text/html"
        return Fetched(url, 200, final_url=url, content_type=ctype, text=body)


def urlset(*urls):
    items = "".join(f"<url><loc>{u}</loc><lastmod>{m}</lastmod></url>" for u, m in urls)
    return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>'


def test_rivals_first_look_then_only_whats_new():
    week1 = {
        "https://rival.test/robots.txt": "Sitemap: https://rival.test/sitemap.xml",
        "https://rival.test/sitemap.xml": urlset(("https://rival.test/a", "2026-10-01"), ("https://rival.test/b", "2026-10-02")),
        "https://rival.test/a": PAGE,
        "https://rival.test/b": PAGE,
    }
    snap, report = rivals.scan(FakeFetcher(week1), "rival.test", None)
    assert report["status"] == "first-look" and report["listed"] == 2 and len(report["pages"]) == 2
    week2 = dict(week1)
    week2["https://rival.test/sitemap.xml"] = urlset(("https://rival.test/a", "2026-11-01"), ("https://rival.test/b", "2026-10-02"), ("https://rival.test/c", "2026-11-03"))
    week2["https://rival.test/c"] = PAGE
    snap2, report2 = rivals.scan(FakeFetcher(week2), "rival.test", snap)
    assert (report2["new"], report2["updated"], report2["removed"]) == (1, 1, 0)
    assert [p["change"] for p in report2["pages"]] == ["new", "updated"]
    assert report2["pages"][0]["title"] == "درمان الف | کلینیک"


def test_an_unreachable_rival_keeps_last_weeks_list():
    previous = {"domain": "rival.test", "scanned": "2026-11-01T00:00Z", "urls": {"https://rival.test/a": "2026-10-01"}}
    snap, report = rivals.scan(FakeFetcher({}), "rival.test", previous)
    assert report["status"] in ("unreachable", "no-sitemap") and snap["urls"] == previous["urls"]


def test_inventory_flags_issues_and_unlisted_pages(site_dir):
    site = load_site(site_dir)
    pages = {
        f"{SITE}/robots.txt": f"Sitemap: {SITE}/sitemap_index.xml",
        f"{SITE}/sitemap_index.xml": f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>{SITE}/page-sitemap.xml</loc></sitemap><sitemap><loc>{SITE}/category-sitemap.xml</loc></sitemap></sitemapindex>',
        f"{SITE}/page-sitemap.xml": urlset((CHANGED, "2026-10-01"), (f"{SITE}/gone/", "2026-09-01")),
        CHANGED: PAGE,
    }
    fetcher = FakeFetcher(pages)
    numbers = {normalise_url(CHANGED): {"clicks": 50, "impressions": 900, "position": 4.0}, normalise_url(f"{SITE}/orphan/"): {"clicks": 3, "impressions": 80, "position": 9.0}}
    inv = inventory.build(site, fetcher, numbers)
    assert inv["listed"] == 2 and inv["read"] == 2
    assert f"{SITE}/category-sitemap.xml" not in fetcher.asked
    by_url = {r["url"]: r for r in inv["pages"]}
    assert by_url[normalise_url(CHANGED)]["clicks_28d"] == 50 and "images without alt" in by_url[normalise_url(CHANGED)]["issues"]
    assert by_url[normalise_url(f"{SITE}/gone/")]["issues"] == "answers 404"
    assert inv["searched_but_not_in_sitemap"][0]["url"] == normalise_url(f"{SITE}/orphan/")


def test_site_config_is_checked(tmp_path):
    (tmp_path / "site.toml").write_text('name = "x"\nkind = "shop"\nproperty = "p"\nbase_url = "https://x.test"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="kind must be"):
        load_site(tmp_path)
    (tmp_path / "site.toml").write_text('name = "x"\nkind = "other"\nproperty = "p"\nbase_url = "https://x.test/"\ncolour = "red"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown setting"):
        load_site(tmp_path)


def test_the_weekly_run_writes_its_folder_and_a_record(bundle, site_dir, tmp_path, monkeypatch):
    import shutil

    from sitewell import gsc

    raw, meta = bundle

    def fake_fetch(session, prop, out, end):
        shutil.copytree(raw, out, dirs_exist_ok=True)
        return meta

    monkeypatch.setattr(gsc, "latest_complete_day", lambda session, prop: END)
    monkeypatch.setattr(gsc, "fetch_bundle", fake_fetch)
    ledger.add(site_dir / "ledger.jsonl", ledger.Change(id="c1", site="clinic", url=CHANGED, kind="title", shipped=SHIPPED.isoformat(), summary="t"))
    ops = tmp_path
    code = week.run(site_dir, ops, as_of=date(2026, 11, 14), skip={"inventory", "rivals"}, session=object())
    assert code == 0
    folder = site_dir / "weeks" / "2026-W46"
    doc = json.loads((folder / "facts.json").read_text(encoding="utf-8"))
    assert doc["search_console"]["data"]["end"] == END.isoformat()
    assert doc["judged_changes"][0]["verdict"] == "gain"
    summary = (folder / "summary.md").read_text(encoding="utf-8")
    assert "Bot burst" in summary and "علت بیماری ب" in summary
    assert (site_dir / "history" / "monthly.csv").exists()
    record = runs.latest(ops, "clinic")
    assert record["status"] == "ok" and record["data_end"] == END.isoformat()
    # Running the same week again is harmless: same folder, no second judgement.
    assert week.run(site_dir, ops, as_of=date(2026, 11, 14), skip={"inventory", "rivals"}, session=object()) == 0
    assert len(ledger.load(site_dir / "ledger.jsonl")[0].judged) == 1


def test_data_start_and_implausible_click_through(bundle, site_dir, tmp_path):
    raw, meta = bundle
    toml = site_dir / "site.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + 'data_start = "2026-08-01"\n', encoding="utf-8")
    site = load_site(site_dir)
    dropped = analysis.prepare(raw, tmp_path / "w", site)
    assert dropped["before_start"] == (date(2026, 8, 1) - (END - timedelta(days=DAYS - 1))).days
    first = next(csv.DictReader((tmp_path / "w" / "Dates.csv").open(encoding="utf-8")))["date"]
    assert first == "2026-08-01"
    # The burst days run at 258 clicks on 2400 impressions: busy, but not implausible.
    assert dropped["implausible"] == []
    fake = tmp_path / "fake.csv"
    write(fake, ["date", "clicks", "impressions"], [{"date": f"2026-01-{d:02d}", "clicks": 90, "impressions": 95} for d in range(10, 15)])
    runs_ = analysis.implausible_days(fake)
    assert runs_ == [{"start": "2026-01-10", "end": "2026-01-14", "clicks": 450, "impressions": 475, "ctr": 0.95}]


def test_live_update_feed_is_parsed_and_wins():
    from sitewell import updates

    entries = updates.parse([
        {"service_name": "Ranking", "begin": "2026-11-10T18:06:00+00:00", "end": None, "external_desc": "November 2026 core update"},
        {"service_name": "Crawling", "begin": "2026-11-01T00:00:00+00:00", "end": None, "external_desc": "Crawl issue"},
    ])
    assert entries == [{"name": "November 2026 core update", "kind": "core", "start": date(2026, 11, 10), "end": None}]


def test_a_rival_without_a_sitemap_is_listed_from_its_home_page():
    pages = {"https://nositemap.test/": '<a href="/a">a</a><a href="https://nositemap.test/b#x">b</a><a href="https://elsewhere.test/">c</a>'}
    snap, report = rivals.scan(FakeFetcher(pages), "nositemap.test", None)
    assert report["list_from"] == "home-page links"
    assert set(snap["urls"]) == {"https://nositemap.test/a", "https://nositemap.test/b"}


def test_a_rival_whose_robots_forbid_sitemaps_says_so():
    class Blocked(FakeFetcher):
        def get(self, url, check_robots=True):
            if url.endswith(".xml"):
                return Fetched(url, None, error="disallowed by robots.txt")
            return super().get(url, check_robots)

    snap, report = rivals.scan(Blocked({}), "blocked.test", None)
    assert report["status"] == "blocked-by-robots"
