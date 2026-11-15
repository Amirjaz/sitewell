"""Site health that can take a site offline overnight: the domain's expiry and status.

A domain can lapse or go on hold at its renewal date with no other warning. RDAP is the
registries' public lookup; rdap.org routes each domain to its registry. Registries without
RDAP (some country domains) come back as unknown, which the report says plainly.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

ALERT_DAYS = 45


def domain(name: str, today: date, session=None, timeout: float = 20.0) -> dict[str, Any]:
    if session is None:
        import requests

        session = requests
    root = ".".join(name.lower().removeprefix("www.").split(".")[-2:]) if not name.endswith((".co.ir", ".ac.ir")) else name
    try:
        r = session.get(f"https://rdap.org/domain/{root}", timeout=timeout, headers={"Accept": "application/rdap+json"})
        if r.status_code != 200:
            return {"domain": root, "status": "unknown", "why": f"RDAP answered {r.status_code}"}
        doc = r.json()
    except Exception as exc:
        return {"domain": root, "status": "unknown", "why": f"{type(exc).__name__}: {exc}"[:160]}
    events = {e.get("eventAction"): e.get("eventDate") for e in doc.get("events", [])}
    expires = events.get("expiration")
    out: dict[str, Any] = {"domain": root, "registry_status": doc.get("status", []), "expires": expires[:10] if expires else None}
    if expires:
        days = (datetime.fromisoformat(expires.replace("Z", "+00:00")).date() - today).days
        out["days_left"] = days
        out["status"] = "expired" if days < 0 else ("renew soon" if days <= ALERT_DAYS else "ok")
    else:
        out["status"] = "unknown"
        out["why"] = "no expiry date in RDAP"
    if any("hold" in s.lower() for s in out["registry_status"]):
        out["status"] = "on hold"
    return out
