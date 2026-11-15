"""Reading web pages politely: robots.txt, sitemaps, and what a page says about itself.

Everything here reads, nothing writes. One request per host at a time, a pause between
requests, an honest user agent, and robots.txt is obeyed for every host but the site's
own (sitewell works for that site, and its owner asked it to read the pages).
"""

from __future__ import annotations

import gzip
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

from . import __version__

USER_AGENT = f"sitewell/{__version__} (+https://github.com/Amirjaz/sitewell)"
MAX_BYTES = 3_000_000
MAX_SITEMAP_URLS = 5_000
WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def normalise_url(url: str) -> str:
    """One spelling per address: decoded path, no fragment, no trailing slash except the root."""
    parts = urlsplit(url.strip())
    path = unquote(parts.path) or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower() or "https", parts.netloc.lower(), path, parts.query, ""))


@dataclass
class Fetched:
    url: str
    status: int | None
    final_url: str | None = None
    content_type: str = ""
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300 and self.error is None


class Fetcher:
    """GETs with a per-host pause and a robots.txt check."""

    def __init__(self, own_hosts: set[str] | None = None, delay: float = 1.0, timeout: float = 25.0, session=None):
        if session is None:
            import requests

            session = requests.Session()
        self.session = session
        self.session.headers["User-Agent"] = USER_AGENT
        self.own = {h.lower().removeprefix("www.") for h in (own_hosts or set())}
        self.delay, self.timeout = delay, timeout
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self.requests = 0

    def _host(self, url: str) -> str:
        return urlsplit(url).netloc.lower()

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = time.monotonic() - last
            if gap < self.delay:
                time.sleep(self.delay - gap)
        self._last[host] = time.monotonic()

    def robots(self, url: str) -> RobotFileParser | None:
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}"
        if key not in self._robots:
            got = self.get(f"{key}/robots.txt", check_robots=False)
            if got.ok:
                parser = RobotFileParser()
                parser.parse(got.text.splitlines())
                self._robots[key] = parser
            else:
                self._robots[key] = None  # no robots.txt: everything allowed
        return self._robots[key]

    def allowed(self, url: str) -> bool:
        if self._host(url).removeprefix("www.") in self.own:
            return True
        parser = self.robots(url)
        return True if parser is None else parser.can_fetch(USER_AGENT, url)

    def get(self, url: str, check_robots: bool = True) -> Fetched:
        if check_robots and not self.allowed(url):
            return Fetched(url, None, error="disallowed by robots.txt")
        self._wait(self._host(url))
        self.requests += 1
        started = time.perf_counter()
        try:
            with self.session.get(url, timeout=self.timeout, stream=True, allow_redirects=True) as r:
                chunks, size = [], 0
                for chunk in r.iter_content(65_536):
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= MAX_BYTES:
                        break
                body = b"".join(chunks)
                if url.endswith(".gz") or body[:2] == b"\x1f\x8b":
                    try:
                        body = gzip.decompress(body)
                    except OSError:
                        pass
                encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else "utf-8"
                return Fetched(
                    url,
                    r.status_code,
                    final_url=r.url,
                    content_type=r.headers.get("content-type", "").split(";")[0].strip(),
                    text=body.decode(encoding, errors="replace"),
                    headers={k.lower(): v for k, v in r.headers.items()},
                    ms=round((time.perf_counter() - started) * 1000),
                )
        except Exception as exc:  # the failure is the finding
            return Fetched(url, None, error=f"{type(exc).__name__}: {exc}"[:200], ms=round((time.perf_counter() - started) * 1000))


# -------------------------------------------------------------------- sitemaps ---


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_sitemap(xml: str) -> tuple[str, list[tuple[str, str | None]]]:
    """('index' or 'urlset', [(loc, lastmod)]). Raises ValueError on something that is not a sitemap."""
    try:
        root = ElementTree.fromstring(xml.lstrip("﻿").strip().encode("utf-8"))
    except ElementTree.ParseError as exc:
        raise ValueError(f"not XML: {exc}") from exc
    kind = _local(root.tag)
    if kind not in ("sitemapindex", "urlset"):
        raise ValueError(f"not a sitemap (root element <{kind}>)")
    rows = []
    for item in root:
        loc = lastmod = None
        for child in item:
            name = _local(child.tag)
            if name == "loc" and child.text:
                loc = child.text.strip()
            elif name == "lastmod" and child.text:
                lastmod = child.text.strip()
        if loc:
            rows.append((loc, lastmod))
    return ("index" if kind == "sitemapindex" else "urlset"), rows


def discover_sitemaps(fetcher: Fetcher, base_url: str) -> list[str]:
    """Sitemaps named in robots.txt, else the usual places."""
    root = base_url.rstrip("/")
    named: list[str] = []
    got = fetcher.get(f"{root}/robots.txt", check_robots=False)
    if got.ok:
        for line in got.text.splitlines():
            if line.lower().startswith("sitemap:"):
                named.append(line.split(":", 1)[1].strip())
    return named or [f"{root}/sitemap.xml", f"{root}/sitemap_index.xml", f"{root}/wp-sitemap.xml"]


@dataclass
class SitemapRead:
    urls: dict[str, str | None] = field(default_factory=dict)  # loc -> lastmod
    sources: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    truncated: bool = False


def read_sitemaps(fetcher: Fetcher, base_url: str, limit: int = MAX_SITEMAP_URLS, skip: tuple[str, ...] = ()) -> SitemapRead:
    """Every page URL the site lists, following sitemap indexes. `skip` drops child sitemaps by substring."""
    out = SitemapRead()
    queue = discover_sitemaps(fetcher, base_url)
    seen: set[str] = set()
    found_any = False
    while queue:
        url = queue.pop(0)
        if url in seen or len(seen) > 60:
            continue
        seen.add(url)
        got = fetcher.get(url)
        if not got.ok:
            out.errors.append(f"{url}: {got.error or got.status}")
            continue
        try:
            kind, rows = parse_sitemap(got.text)
        except ValueError as exc:
            out.errors.append(f"{url}: {exc}")
            continue
        found_any = True
        out.sources.append(url)
        if kind == "index":
            queue.extend(loc for loc, _ in rows if not any(s in loc for s in skip))
            continue
        for loc, lastmod in rows:
            if len(out.urls) >= limit:
                out.truncated = True
                break
            out.urls[loc] = lastmod
    if found_any:
        # The fallback guesses that 404'd are not worth reporting once a real sitemap was found.
        out.errors = [e for e in out.errors if ": 404" not in e]
    return out


# ----------------------------------------------------------------------- pages ---


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() if node else ""


def _schema_types(data: Any, found: list[str]) -> None:
    if isinstance(data, list):
        for item in data:
            _schema_types(item, found)
    elif isinstance(data, dict):
        t = data.get("@type")
        for name in t if isinstance(t, list) else [t]:
            if isinstance(name, str) and name not in found:
                found.append(name)
        for key in ("@graph", "mainEntity", "author", "publisher", "itemListElement"):
            if key in data:
                _schema_types(data[key], found)


def _schema_dates(data: Any, out: dict[str, str]) -> None:
    if isinstance(data, list):
        for item in data:
            _schema_dates(item, out)
    elif isinstance(data, dict):
        for key in ("dateModified", "datePublished"):
            if isinstance(data.get(key), str) and key not in out:
                out[key] = data[key]
        if "@graph" in data:
            _schema_dates(data["@graph"], out)


def parse_page(html: str, url: str) -> dict[str, Any]:
    """What a page says about itself: the fields an SEO review looks at first."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    head_meta = {
        (m.get("name") or m.get("property") or "").lower(): (m.get("content") or "").strip()
        for m in soup.find_all("meta")
        if m.get("name") or m.get("property")
    }
    canonical = soup.find("link", rel=lambda v: v and "canonical" in (v if isinstance(v, list) else [v]))
    html_tag = soup.find("html")

    types: list[str] = []
    dates: dict[str, str] = {}
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        _schema_types(data, types)
        _schema_dates(data, dates)

    host = urlsplit(url).netloc.lower().removeprefix("www.")
    internal = external = 0
    targets: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(url, href)
        target = urlsplit(full).netloc.lower().removeprefix("www.")
        if target == host:
            internal += 1
            n = normalise_url(full.split("#")[0])
            if n not in targets:
                targets.append(n)
        elif target:
            external += 1

    images = soup.find_all("img")
    image_count = len(images)
    images_without_alt = sum(1 for i in images if not (i.get("alt") or "").strip())
    # Removing a parent removes its children too; touching a removed child again raises.
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        if not tag.decomposed:
            tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    for tag in main.find_all(["nav", "footer", "header", "aside"]):
        if not tag.decomposed:
            tag.decompose()
    words = len(WORD.findall(main.get_text(" ")))

    h1 = [_text(h) for h in soup.find_all("h1")]
    h2 = [_text(h) for h in soup.find_all("h2")]
    title = _text(soup.title)
    return {
        "title": title,
        "description": head_meta.get("description", ""),
        "robots": head_meta.get("robots", ""),
        "noindex": "noindex" in head_meta.get("robots", "").lower(),
        "canonical": urljoin(url, canonical["href"]) if canonical and canonical.get("href") else None,
        "lang": (html_tag.get("lang") if html_tag else None) or None,
        "dir": (html_tag.get("dir") if html_tag else None) or None,
        "h1": h1,
        "h2": h2[:40],
        "h2_count": len(h2),
        "h3_count": len(soup.find_all("h3")),
        "words": words,
        "schema_types": types,
        "modified": head_meta.get("article:modified_time") or dates.get("dateModified"),
        "published": head_meta.get("article:published_time") or dates.get("datePublished"),
        "internal_links": internal,
        "internal_targets": targets[:250],
        "external_links": external,
        "images": image_count,
        "images_without_alt": images_without_alt,
    }


def extract_content(html: str, url: str, max_chars: int = 14_000) -> dict[str, Any]:
    """A page as the writer needs it: title, description, headings and text blocks in order,
    internal links with their anchors, and the structured data as served."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    meta = {(m.get("name") or m.get("property") or "").lower(): (m.get("content") or "").strip() for m in soup.find_all("meta") if m.get("name") or m.get("property")}
    jsonld = [s.string.strip() for s in soup.find_all("script", type="application/ld+json") if s.string]
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        if not tag.decomposed:
            tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    for tag in main.find_all(["nav", "footer", "header", "aside"]):
        if not tag.decomposed:
            tag.decompose()
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    links = []
    for a in main.find_all("a", href=True):
        target = urljoin(url, a["href"].strip())
        if urlsplit(target).netloc.lower().removeprefix("www.") == host:
            links.append({"anchor": _text(a), "href": normalise_url(target)})
    blocks, size = [], 0
    for el in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "blockquote"]):
        if el.find(["p", "li"]) is not None and el.name in ("li", "td", "blockquote"):
            continue  # its children are listed on their own
        text = _text(el)
        if not text or (blocks and blocks[-1]["text"] == text):
            continue
        blocks.append({"tag": el.name, "text": text})
        size += len(text)
        if size > max_chars:
            blocks.append({"tag": "note", "text": "(page text cut here for length)"})
            break
    return {
        "url": normalise_url(url),
        "title": _text(soup.title),
        "description": meta.get("description", ""),
        "canonical": normalise_url(urljoin(url, c["href"])) if (c := soup.find("link", rel=lambda v: v and "canonical" in (v if isinstance(v, list) else [v]))) and c.get("href") else None,
        "blocks": blocks,
        "internal_links": links[:150],
        "jsonld": jsonld[:6],
    }
