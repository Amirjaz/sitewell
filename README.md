# sitewell

An SEO agent that looks after a site on a schedule. Code measures, Claude decides, and
only what the rules allow gets shipped.

**Status: early.** This repository is at Phase 0, which proves the three things
everything else depends on. Nothing here changes a site yet.

## The idea

Most "AI SEO" tools write a lot and measure little. sitewell is built the other way
round:

- **Code computes every number.** Clicks, baselines, bot bursts and gaps come from
  Python, never from a model reading a table.
- **Claude only decides and writes,** inside written rules, and its output is checked
  before anything uses it.
- **The model never holds site passwords.** The step that reads other people's pages has
  no write access. Publishing is plain code that runs after it.
- **A new page needs evidence:** a query with real impressions that no existing page can
  cover. Some weeks publish nothing new.
- **A person approves every new article** by merging a pull request.

It runs on GitHub Actions, on a Claude subscription token or an API key.

## What works today

```bash
pip install "sitewell @ git+https://github.com/Amirjaz/sitewell"
```

Search Console, through Google's own API:

```bash
sitewell gsc auth --client-secrets client.json --out gsc-credentials.json
export SITEWELL_GSC_CREDENTIALS_FILE=gsc-credentials.json

sitewell gsc sites
sitewell gsc fetch sc-domain:example.com --out data/example
sitewell gsc inspect sc-domain:example.com https://example.com/some-page/
```

`fetch` writes `daily.csv`, `queries.csv` and `pages.csv` for about 16 months.
[docs/google-cloud-setup.md](docs/google-cloud-setup.md) walks through the one-time
Google setup.

Reachability, to run from wherever the agent will run:

```bash
sitewell reach https://example.com/ https://example.com/sitemap.xml
```

Each URL is requested as a browser and with Googlebot's user agent, and the result is a
table of status, time and size.

## WordPress

[wp-plugin/sitewell-bridge](wp-plugin/sitewell-bridge) is a small plugin that lets the
agent read and write SEO titles and descriptions (Yoast or Rank Math) and clear page
caches over the REST API. It also accepts the application password in its own header,
for hosts that strip `Authorization`.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"      # Windows: .venv\Scripts\pip
.venv/bin/pytest
```

## License

MIT
