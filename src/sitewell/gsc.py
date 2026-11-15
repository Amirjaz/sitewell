"""Search Console access: sign-in, Search Analytics rows and URL inspection.

Credentials are one JSON document, in either of two shapes:

* an "authorized_user" document, written by `sitewell gsc auth` after you sign in once
  in a browser. It works with whatever access your Google account already has.
* a service-account key. It needs no browser, but the service account's email has to be
  added as a user on every Search Console property first.

The document is read from the file named by SITEWELL_GSC_CREDENTIALS_FILE, or from the
SITEWELL_GSC_CREDENTIALS variable itself, which is the form a GitHub Actions secret takes.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import quote

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
# Google serves this API from two hosts. www.googleapis.com is cut off on some networks
# (in Iran, for one) where this one still answers.
API = "https://searchconsole.googleapis.com/webmasters/v3"
INSPECT = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"

ROW_LIMIT = 25_000  # the most rows one Search Analytics request returns
HISTORY_DAYS = 486  # Search Console keeps about 16 months
LAG_DAYS = 3  # the newest days are still being counted
METRICS = ("clicks", "impressions", "ctr", "position")


class GscError(RuntimeError):
    """Search Console said no. The message carries the status and Google's reason."""


# ------------------------------------------------------------- credentials ---


def credentials_info(env: dict[str, str] | None = None) -> dict[str, Any]:
    """The credentials document, from the file or the variable. Raises if neither is set."""
    env = os.environ if env is None else env
    path = env.get("SITEWELL_GSC_CREDENTIALS_FILE")
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    raw = env.get("SITEWELL_GSC_CREDENTIALS")
    if raw:
        return json.loads(raw)
    raise GscError(
        "No Search Console credentials. Set SITEWELL_GSC_CREDENTIALS_FILE to a JSON file, "
        "or SITEWELL_GSC_CREDENTIALS to its contents. `sitewell gsc auth` creates the file."
    )


def load_credentials(info: dict[str, Any] | None = None):
    """Google credentials for either document shape."""
    info = credentials_info() if info is None else info
    if info.get("type") == "service_account":
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    if "refresh_token" in info:
        from google.oauth2.credentials import Credentials

        return Credentials.from_authorized_user_info(info, scopes=SCOPES)
    raise GscError(
        "The credentials document is neither a service-account key nor a signed-in user. "
        "If it is the OAuth client file from Google Cloud, run `sitewell gsc auth` with it."
    )


def open_session(info: dict[str, Any] | None = None):
    """A requests session that signs every call and refreshes the token when needed."""
    from google.auth.transport.requests import AuthorizedSession

    return AuthorizedSession(load_credentials(info))


def authorize(client_secrets: Path, out: Path) -> Path:
    """Sign in once in the browser and save a document that keeps working afterwards.

    `client_secrets` is the "Desktop app" OAuth client file downloaded from Google Cloud.
    The saved document holds a refresh token: keep it out of git.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes=SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        raise GscError("Google returned no refresh token. Remove the app's access in your Google account and try again.")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json(), encoding="utf-8")
    return out


# -------------------------------------------------------------------- calls ---


def _send(method, url: str, **kwargs):
    """One call, with connection failures turned into a GscError that says what to try."""
    import requests

    try:
        return method(url, **kwargs)
    except requests.exceptions.ConnectionError as exc:
        host = url.split("/")[2]
        raise GscError(
            f"Could not reach {host}: the connection was cut off. On a network that blocks "
            "Google's APIs, route googleapis.com through your VPN or set HTTPS_PROXY, "
            "or run from a machine outside that network (a GitHub runner, for example)."
        ) from exc


def _check(response) -> dict[str, Any]:
    if response.status_code == 403 and "text/html" in response.headers.get("content-type", ""):
        raise GscError(
            "HTTP 403 from Google's front door, not from the API: the request came from an address "
            "Google refuses (a sanctioned country, for example). Use a VPN route or a machine elsewhere."
        )
    if response.status_code >= 400:
        try:
            reason = response.json()["error"]["message"]
        except Exception:
            reason = response.text[:300]
        raise GscError(f"HTTP {response.status_code}: {reason}")
    return response.json() if response.content else {}


def list_sites(session) -> list[dict[str, str]]:
    """Every property the credentials can see, with the permission level on each."""
    data = _check(_send(session.get, f"{API}/sites", timeout=60))
    return sorted(data.get("siteEntry", []), key=lambda s: s["siteUrl"])


def history_window(today: date | None = None, days: int = HISTORY_DAYS) -> tuple[date, date]:
    """The longest window worth asking for: it ends before the days still being counted."""
    end = (today or date.today()) - timedelta(days=LAG_DAYS)
    return end - timedelta(days=days - 1), end


def query(
    session,
    site: str,
    start: date,
    end: date,
    dimensions: Iterable[str],
    *,
    search_type: str = "web",
    data_state: str = "final",
    row_limit: int = ROW_LIMIT,
) -> Iterator[dict[str, Any]]:
    """Search Analytics rows as flat dicts: one key per dimension, then the four metrics.

    Pages through the result until Google returns a short page. With a `query` or `page`
    dimension Google leaves out rare queries, so take site totals from `["date"]` alone.
    """
    dims = list(dimensions)
    url = f"{API}/sites/{quote(site, safe='')}/searchAnalytics/query"
    start_row = 0
    while True:
        body = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": dims,
            "type": search_type,
            "dataState": data_state,
            "rowLimit": row_limit,
            "startRow": start_row,
        }
        rows = _check(_send(session.post, url, json=body, timeout=120)).get("rows", [])
        for row in rows:
            flat = dict(zip(dims, row.get("keys", [])))
            flat.update({m: row.get(m, 0) for m in METRICS})
            yield flat
        if len(rows) < row_limit:
            return
        start_row += row_limit


def inspect(session, site: str, url: str, language: str = "en-US") -> dict[str, Any]:
    """What Google's index says about one URL of a property, flattened to the useful fields."""
    body = {"inspectionUrl": url, "siteUrl": site, "languageCode": language}
    result = _check(_send(session.post, INSPECT, json=body, timeout=120)).get("inspectionResult", {})
    index = result.get("indexStatusResult", {})
    return {
        "url": url,
        "verdict": index.get("verdict"),
        "coverage": index.get("coverageState"),
        "indexing": index.get("indexingState"),
        "fetch": index.get("pageFetchState"),
        "robots": index.get("robotsTxtState"),
        "last_crawl": index.get("lastCrawlTime"),
        "google_canonical": index.get("googleCanonical"),
        "user_canonical": index.get("userCanonical"),
    }


# -------------------------------------------------------------------- files ---

PULLS = {
    "daily": ["date"],
    "queries": ["date", "query"],
    "pages": ["date", "page"],
}

# The weekly bundle: everything the detectors read. The query-by-page table is large, so it
# only covers the most recent weeks, which is all the cannibalisation and coverage checks use.
# "Dates.csv" is named the way gsc-pulse expects a Search Console daily table.
BUNDLE = {
    "Dates": (["date"], None),
    "queries": (["date", "query"], None),
    "pages": (["date", "page"], None),
    "countries": (["date", "country"], None),
    "devices": (["date", "device"], None),
    "query_pages": (["date", "query", "page"], 56),
}


def fetch_history(session, site: str, out: Path, start: date, end: date) -> dict[str, int]:
    """Write daily.csv, queries.csv and pages.csv for a property. Returns rows per file."""
    out.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for name, dims in PULLS.items():
        path = out / f"{name}.csv"
        n = 0
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=[*dims, *METRICS])
            writer.writeheader()
            for row in query(session, site, start, end, dims):
                writer.writerow(row)
                n += 1
        counts[name] = n
    meta = {"site": site, "start": start.isoformat(), "end": end.isoformat(), "rows": counts}
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return counts


def fetch_bundle(session, site: str, out: Path, end: date, history_days: int = HISTORY_DAYS) -> dict[str, Any]:
    """Write the weekly bundle for a property into `out`. Returns the window and rows per file."""
    out.mkdir(parents=True, exist_ok=True)
    start = end - timedelta(days=history_days - 1)
    counts: dict[str, int] = {}
    for name, (dims, days) in BUNDLE.items():
        first = start if days is None else end - timedelta(days=days - 1)
        path = out / f"{name}.csv"
        n = 0
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=[*dims, *METRICS])
            writer.writeheader()
            for row in query(session, site, first, end, dims):
                writer.writerow(row)
                n += 1
        counts[name] = n
    meta = {"site": site, "start": start.isoformat(), "end": end.isoformat(), "rows": counts}
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def latest_complete_day(session, site: str, today: date | None = None) -> date:
    """The newest day Search Console reports as final, looking back at most a week."""
    today = today or date.today()
    rows = list(query(session, site, today - timedelta(days=10), today, ["date"]))
    days = sorted(date.fromisoformat(r["date"]) for r in rows if r.get("impressions"))
    return days[-1] if days else today - timedelta(days=LAG_DAYS)
