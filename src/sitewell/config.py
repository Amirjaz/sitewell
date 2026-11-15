"""One site's settings, read from its `site.toml`."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

KINDS = ("nextjs", "wordpress", "other")


class ConfigError(ValueError):
    """A site.toml that is missing something or holds a value sitewell can't use."""


@dataclass
class Site:
    name: str
    kind: str
    property: str
    base_url: str
    folder: Path
    brand_terms: list[str] = field(default_factory=list)
    brand_patterns: list[str] = field(default_factory=list)
    # Queries matching any of these are not about the site at all (a crossword clue, say):
    # they are dropped before anything is measured.
    ignore_query_patterns: list[str] = field(default_factory=list)
    rivals: list[str] = field(default_factory=list)
    reach: list[str] = field(default_factory=lambda: ["/"])
    max_pages: int = 300
    # Search Console data before this ISO date is ignored: a relaunch, a takeover, a period of
    # fake traffic. Everything (baselines, detectors, history) starts here.
    data_start: str | None = None
    seo_plugin: str | None = None
    repo: str | None = None
    host: str | None = None

    @property
    def domain(self) -> str:
        return re.sub(r"^https?://", "", self.base_url).split("/")[0].lower()

    def ignored(self, query: str) -> bool:
        return any(re.search(p, query) for p in self.ignore_query_patterns)


def load_site(path: Path | str) -> Site:
    """Read a site folder (holding site.toml) or a site.toml file."""
    path = Path(path)
    file = path / "site.toml" if path.is_dir() else path
    if not file.exists():
        raise ConfigError(f"{file} does not exist")
    data = tomllib.loads(file.read_text(encoding="utf-8"))
    for key in ("name", "kind", "property", "base_url"):
        if not data.get(key):
            raise ConfigError(f"{file}: '{key}' is required")
    if data["kind"] not in KINDS:
        raise ConfigError(f"{file}: kind must be one of {', '.join(KINDS)}")
    known = {f for f in Site.__dataclass_fields__ if f != "folder"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"{file}: unknown setting(s) {', '.join(unknown)}")
    for pattern in data.get("brand_patterns", []) + data.get("ignore_query_patterns", []):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(f"{file}: bad pattern {pattern!r}: {exc}") from exc
    rivals = [re.sub(r"^https?://", "", r).strip("/").lower() for r in data.get("rivals", [])]
    if data.get("data_start"):
        try:
            date.fromisoformat(str(data["data_start"]))
        except ValueError as exc:
            raise ConfigError(f"{file}: data_start must be a date like 2026-03-01") from exc
        data["data_start"] = str(data["data_start"])
    return Site(folder=file.parent, **{**data, "base_url": data["base_url"].rstrip("/"), "rivals": rivals})
