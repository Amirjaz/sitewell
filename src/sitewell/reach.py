"""Can this machine reach a site, and does the site treat a crawler differently?

Run from the place the agent will run (a GitHub runner), this answers the question
before anything is built on top of it. Each URL is requested twice: once as an ordinary
browser and once with Googlebot's user agent. The second is only a hint. A firewall that
verifies Googlebot by IP address will treat it as a fake, which is worth knowing too.
"""

from __future__ import annotations

import time
from typing import Any

AGENTS = {
    "browser": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "googlebot-ua": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
}
MAX_BYTES = 512_000


def probe(url: str, agent: str, timeout: float = 25.0, get=None) -> dict[str, Any]:
    """One GET. Never raises: a failure is a row with `error` set."""
    if get is None:
        import requests

        get = requests.get
    row: dict[str, Any] = {"url": url, "agent": agent, "status": None, "ms": None, "bytes": 0}
    started = time.perf_counter()
    try:
        with get(url, headers={"User-Agent": AGENTS[agent]}, timeout=timeout, stream=True) as r:
            size = 0
            for chunk in r.iter_content(65_536):
                size += len(chunk)
                if size >= MAX_BYTES:
                    break
            row.update(
                status=r.status_code,
                bytes=size,
                final_url=r.url,
                server=r.headers.get("server", ""),
                content_type=r.headers.get("content-type", "").split(";")[0],
            )
    except Exception as exc:  # network errors are the finding, not a crash
        row["error"] = f"{type(exc).__name__}: {exc}"[:200]
    row["ms"] = round((time.perf_counter() - started) * 1000)
    return row


def probe_all(urls: list[str], get=None) -> list[dict[str, Any]]:
    return [probe(url, agent, get=get) for url in urls for agent in AGENTS]


def as_markdown(rows: list[dict[str, Any]]) -> str:
    lines = ["| URL | As | Status | Time | Size | Note |", "|---|---|---|---|---|---|"]
    for r in rows:
        note = r.get("error") or ""
        if not note and r.get("final_url") and r["final_url"].rstrip("/") != r["url"].rstrip("/"):
            note = f"redirected to {r['final_url']}"
        status = r["status"] if r["status"] is not None else "failed"
        lines.append(f"| {r['url']} | {r['agent']} | {status} | {r['ms']} ms | {r['bytes']:,} B | {note} |")
    return "\n".join(lines)


def all_ok(rows: list[dict[str, Any]]) -> bool:
    """True when every browser request answered below 400. The crawler rows are advisory."""
    return all(r["status"] is not None and r["status"] < 400 for r in rows if r["agent"] == "browser")
