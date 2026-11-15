"""The `sitewell` command."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import __version__, gsc, ledger, reach, runs, shadow, week


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sitewell", description="An SEO agent that looks after a site on a schedule.")
    parser.add_argument("--version", action="version", version=f"sitewell {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    g = commands.add_parser("gsc", help="Google Search Console").add_subparsers(dest="action", required=True)

    auth = g.add_parser("auth", help="sign in once in a browser and save the credentials file")
    auth.add_argument("--client-secrets", type=Path, required=True, help="the Desktop app OAuth client file from Google Cloud")
    auth.add_argument("--out", type=Path, required=True, help="where to save the signed-in credentials (keep it out of git)")

    g.add_parser("sites", help="list the properties these credentials can see")

    fetch = g.add_parser("fetch", help="write daily.csv, queries.csv and pages.csv for a property")
    fetch.add_argument("site", help="the property, for example sc-domain:example.com or https://example.com/")
    fetch.add_argument("--out", type=Path, required=True)
    fetch.add_argument("--start", type=date.fromisoformat, help="first day, default about 16 months back")
    fetch.add_argument("--end", type=date.fromisoformat, help="last day, default three days ago")

    ins = g.add_parser("inspect", help="index status of one or more URLs")
    ins.add_argument("site")
    ins.add_argument("urls", nargs="+")

    r = commands.add_parser("reach", help="request URLs as a browser and as a crawler, and report what came back")
    r.add_argument("urls", nargs="+")
    r.add_argument("--json", type=Path, help="also write the rows to this file")

    w = commands.add_parser("week", help="one site's weekly data run: fetch, detect, inventory, rivals, judgements")
    w.add_argument("site", type=Path, help="the site's folder (holding site.toml)")
    w.add_argument("--ops-root", type=Path, default=Path("."), help="where runs/ records go (default: here)")
    w.add_argument("--as-of", type=date.fromisoformat, help="date that names the week folder (default: today)")
    w.add_argument("--raw", type=Path, help="keep the raw Search Console bundle here instead of a temporary folder")
    w.add_argument("--skip", default="", help="comma-separated: inventory, rivals")

    sh = commands.add_parser("shadow", help="decide and write this week's actions without shipping anything")
    sh.add_argument("site", type=Path)
    sh.add_argument("--ops-root", type=Path, default=Path("."))

    wa = commands.add_parser("watch", help="exit 1 if any site has no recent successful weekly run")
    wa.add_argument("--ops-root", type=Path, default=Path("."))
    wa.add_argument("--max-age-days", type=float, default=8.0)

    lg = commands.add_parser("ledger", help="the change ledger").add_subparsers(dest="action", required=True)
    la = lg.add_parser("add", help="record a change that went live")
    la.add_argument("site", type=Path)
    la.add_argument("--id", required=True)
    la.add_argument("--url", required=True)
    la.add_argument("--kind", required=True, choices=ledger.KINDS)
    la.add_argument("--shipped", required=True, type=date.fromisoformat)
    la.add_argument("--summary", required=True)
    la.add_argument("--before")
    la.add_argument("--after")
    la.add_argument("--ref")
    la.add_argument("--source", default="sitewell", choices=["sitewell", "imported"])
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    try:
        if args.command == "week":
            skip = {s.strip() for s in args.skip.split(",") if s.strip()}
            return week.run(args.site, args.ops_root, args.as_of, args.raw, skip)

        if args.command == "shadow":
            return shadow.run(args.site, args.ops_root)

        if args.command == "watch":
            sites = sorted(p.parent.name for p in (args.ops_root / "sites").glob("*/site.toml"))
            problems = runs.check(args.ops_root, sites, args.max_age_days)
            for p in problems:
                print(p)
            if not problems:
                print(f"All {len(sites)} sites have a recent successful weekly run.")
            return 1 if problems else 0

        if args.command == "ledger":
            from .config import load_site

            site = load_site(args.site)
            change = ledger.Change(
                id=args.id, site=site.name, url=args.url, kind=args.kind, shipped=args.shipped.isoformat(),
                summary=args.summary, before=args.before, after=args.after, ref=args.ref, source=args.source,
            )
            ledger.add(site.folder / "ledger.jsonl", change)
            print(f"Added {args.id} to {site.folder / 'ledger.jsonl'}")
            return 0

        if args.command == "reach":
            rows = reach.probe_all(args.urls)
            if args.json:
                args.json.parent.mkdir(parents=True, exist_ok=True)
                args.json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
            print(reach.as_markdown(rows))
            return 0 if reach.all_ok(rows) else 1

        if args.action == "auth":
            path = gsc.authorize(args.client_secrets, args.out)
            print(f"Signed in. Credentials saved to {path}")
            return 0

        from google.auth.transport.requests import Request

        session = gsc.open_session()
        try:
            session.credentials.refresh(Request())
        except Exception as exc:  # the token request itself failed: almost always the network
            raise gsc.GscError(
                f"Could not sign in to Google ({type(exc).__name__}). Check the network route to oauth2.googleapis.com."
            ) from exc
        if args.action == "sites":
            sites = gsc.list_sites(session)
            for site in sites:
                print(f"{site['permissionLevel']:<22} {site['siteUrl']}")
            if not sites:
                who = getattr(session.credentials, "service_account_email", None)
                hint = f" Add {who} as a user on each property in Search Console." if who else ""
                print(f"These credentials can see no Search Console property.{hint}")
                return 3
        elif args.action == "fetch":
            start, end = gsc.history_window()
            start, end = args.start or start, args.end or end
            counts = gsc.fetch_history(session, args.site, args.out, start, end)
            print(f"{args.site}: {start} to {end}")
            for name, n in counts.items():
                print(f"  {name}.csv  {n:,} rows")
        elif args.action == "inspect":
            print(json.dumps([gsc.inspect(session, args.site, url) for url in args.urls], indent=2, ensure_ascii=False))
        return 0
    except gsc.GscError as exc:
        print(f"sitewell: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
