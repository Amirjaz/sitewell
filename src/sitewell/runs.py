"""Run records, and the check that a weekly run actually happened.

GitHub's schedule starts runs late and sometimes not at all, and a run can die before it
reports anything. So every run writes a small record of itself, and a separate daily check
looks for the records instead of trusting that the schedule fired.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def now() -> datetime:
    return datetime.now(timezone.utc)


def record(ops_root: Path, site: str, kind: str, started: datetime, status: str, steps: list[dict[str, Any]], extra: dict[str, Any] | None = None) -> Path:
    path = ops_root / "runs" / site / f"{started.strftime('%Y-%m-%dT%H%MZ')}-{kind}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "site": site,
        "kind": kind,
        "status": status,  # ok | partial | failed
        "started": started.isoformat(timespec="seconds"),
        "finished": now().isoformat(timespec="seconds"),
        "steps": steps,
        **(extra or {}),
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def latest(ops_root: Path, site: str, kind: str = "weekly", ok_only: bool = True) -> dict[str, Any] | None:
    folder = ops_root / "runs" / site
    if not folder.exists():
        return None
    for path in sorted(folder.glob(f"*-{kind}.json"), reverse=True):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not ok_only or doc.get("status") in ("ok", "partial"):
            return doc
    return None


def check(ops_root: Path, sites: list[str], max_age_days: float = 8.0, at: datetime | None = None) -> list[str]:
    """Problems worth an alert: a site with no recent good weekly run, or whose last run failed."""
    at = at or now()
    problems = []
    for site in sites:
        good = latest(ops_root, site)
        last = latest(ops_root, site, ok_only=False)
        if good is None:
            problems.append(f"{site}: no successful weekly run recorded")
        else:
            age = at - datetime.fromisoformat(good["started"])
            if age > timedelta(days=max_age_days):
                problems.append(f"{site}: last successful weekly run was {age.days} days ago ({good['started']})")
        if last is not None and last.get("status") == "failed" and (good is None or last["started"] > good["started"]):
            failed = [s["name"] for s in last.get("steps", []) if s.get("status") == "failed"]
            problems.append(f"{site}: the latest weekly run failed ({', '.join(failed) or 'see its record'})")
    return problems
