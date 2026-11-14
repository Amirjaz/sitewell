import json
from datetime import date

import pytest

from sitewell import gsc, reach


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status
        self.content = b"x"
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    """Serves Search Analytics pages from a list of rows and records every request body."""

    def __init__(self, rows):
        self.rows, self.bodies, self.urls = rows, [], []

    def post(self, url, json, timeout):
        self.urls.append(url)
        self.bodies.append(json)
        start = json["startRow"]
        return FakeResponse({"rows": self.rows[start : start + json["rowLimit"]]})


def row(i):
    return {"keys": [f"2026-10-{i % 28 + 1:02d}", f"query {i}"], "clicks": i, "impressions": i * 10, "ctr": 0.1, "position": 4.2}


def test_query_pages_until_a_short_page():
    session = FakeSession([row(i) for i in range(7)])
    rows = list(gsc.query(session, "sc-domain:example.com", date(2026, 10, 1), date(2026, 10, 28), ["date", "query"], row_limit=3))
    assert len(rows) == 7
    assert [b["startRow"] for b in session.bodies] == [0, 3, 6]
    assert rows[5] == {"date": "2026-10-06", "query": "query 5", "clicks": 5, "impressions": 50, "ctr": 0.1, "position": 4.2}


def test_query_asks_once_more_when_the_last_page_is_full():
    session = FakeSession([row(i) for i in range(6)])
    assert len(list(gsc.query(session, "https://example.com/", date(2026, 10, 1), date(2026, 10, 2), ["date", "query"], row_limit=3))) == 6
    assert [b["startRow"] for b in session.bodies] == [0, 3, 6]


def test_property_is_encoded_in_the_path():
    session = FakeSession([])
    list(gsc.query(session, "https://example.com/", date(2026, 10, 1), date(2026, 10, 2), ["date"]))
    assert session.urls[0].endswith("/sites/https%3A%2F%2Fexample.com%2F/searchAnalytics/query")


def test_history_window_ends_before_the_days_still_being_counted():
    start, end = gsc.history_window(date(2026, 11, 14))
    assert end == date(2026, 11, 11)
    assert (end - start).days == gsc.HISTORY_DAYS - 1


def test_errors_carry_googles_reason():
    with pytest.raises(gsc.GscError, match="HTTP 403: User does not have sufficient permission"):
        gsc._check(FakeResponse({"error": {"message": "User does not have sufficient permission"}}, status=403))


def test_credentials_come_from_the_variable_or_the_file(tmp_path):
    doc = {"type": "service_account", "client_email": "a@b.iam.gserviceaccount.com"}
    assert gsc.credentials_info({"SITEWELL_GSC_CREDENTIALS": json.dumps(doc)}) == doc
    path = tmp_path / "creds.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert gsc.credentials_info({"SITEWELL_GSC_CREDENTIALS_FILE": str(path)}) == doc
    with pytest.raises(gsc.GscError, match="No Search Console credentials"):
        gsc.credentials_info({})


def test_an_oauth_client_file_is_not_mistaken_for_credentials():
    with pytest.raises(gsc.GscError, match="run `sitewell gsc auth`"):
        gsc.load_credentials({"installed": {"client_id": "x"}})


def test_fetch_history_writes_three_tables(tmp_path):
    session = FakeSession([row(i) for i in range(4)])
    counts = gsc.fetch_history(session, "sc-domain:example.com", tmp_path, date(2026, 10, 1), date(2026, 10, 28))
    assert counts == {"daily": 4, "queries": 4, "pages": 4}
    assert (tmp_path / "queries.csv").read_text(encoding="utf-8").splitlines()[0] == "date,query,clicks,impressions,ctr,position"
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))["site"] == "sc-domain:example.com"


class FakeGet:
    def __init__(self, status, fail=False):
        self.status, self.fail = status, fail

    def __call__(self, url, headers, timeout, stream):
        if self.fail:
            raise ConnectionError("refused")
        self.status_code, self.url, self.headers = self.status, url, {"server": "nginx", "content-type": "text/html; charset=utf-8"}
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, size):
        yield b"x" * 100


def test_probe_reports_each_url_twice_and_never_raises():
    rows = reach.probe_all(["https://example.com/"], get=FakeGet(200))
    assert [r["agent"] for r in rows] == ["browser", "googlebot-ua"]
    assert rows[0]["status"] == 200 and rows[0]["bytes"] == 100 and rows[0]["content_type"] == "text/html"
    assert reach.all_ok(rows)

    failed = reach.probe_all(["https://example.com/"], get=FakeGet(0, fail=True))
    assert failed[0]["status"] is None and "ConnectionError" in failed[0]["error"]
    assert not reach.all_ok(failed)
    assert "| failed |" in reach.as_markdown(failed)
