# sitewell

An SEO agent that looks after a site on a schedule. Code measures, Claude decides, and
only what the rules allow gets shipped.

**Status: early.** It runs weekly on two real sites in shadow mode: it measures, decides
and writes every week, but nothing is shipped yet. Shipping comes next.

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

## The weekly data run

Each site is a folder with a `site.toml`:

```toml
name = "example"
kind = "wordpress"                 # nextjs | wordpress | other
property = "sc-domain:example.com" # the Search Console property
base_url = "https://example.com"
brand_terms = ["example clinic"]
brand_patterns = ['^dr example(\s|$)']
ignore_query_patterns = ['crossword$'] # queries that are never about the site
rivals = ["rival-one.com", "rival-two.com"]
data_start = "2026-03-01"          # optional: ignore earlier, unreliable data
```

```bash
sitewell week sites/example --ops-root .
```

One run, safe to repeat:

1. **Fetches about 16 months of Search Console data:** by day, query, page, country and
   device.
2. **Runs [gsc-pulse](https://github.com/Amirjaz/gsc-pulse)'s detectors:** bot-click bursts,
   traffic shifts matched to Google updates (read live from the Search Status Dashboard),
   click-through gaps, striking distance and cannibalisation.
3. **Builds a weekly non-brand baseline** with bot bursts taken out.
4. **Lists the evidence for new pages:** queries with real demand that no page on the site
   answers well.
5. **Reads every page in the sitemap** and joins it with its search numbers.
6. **Reads each rival's sitemap** and reports what they published or changed since last week.
7. **Judges past changes** in the ledger at 14 and 28 days, against the page's own numbers
   and the whole site's move.

It writes `weeks/<ISO week>/facts.json`, `summary.md` and `inventory.csv`. It also adds to
`history/monthly.csv` and records itself in `runs/`. `sitewell watch` fails when a site has
no successful weekly run in the last eight days, because schedulers miss runs.

## The weekly shadow run

```bash
sitewell shadow sites/example --ops-root .
```

After the data run, Claude decides and writes the week's actions, and plain code decides
what may happen to them:

1. **Code settles the limits first:**
   - pages changed in the last 14 days are locked;
   - at most two new pages a month, and only for a query in the new-page evidence;
   - changes pause while a Google update is rolling out or the domain is in trouble.
2. **Claude decides on a strong model,** with the site's `rules.md`, the facts, the page
   inventory, the link map and the change ledger. It proposes at most five actions, each
   citing its numbers.
3. **Code drops what the limits don't allow.**
4. **Code fetches the current content** of every page involved.
5. **Claude writes the changes:** new titles and descriptions, links, small text edits,
   structured data, or a full draft for a new page.
6. **Code checks every draft:**
   - forbidden phrases, such as superlatives;
   - edits whose "before" text isn't on the page;
   - links to pages that don't exist;
   - rewording of existing text;
   - edits over 1,200 characters.
7. **The smallest model checks** for new medical claims, Persian language problems,
   overlap with other pages, and the site's rules.
8. **Every action ends up in one state:** would go live, waits for you, for you to do,
   blocked, held, dropped, or no change needed.

Each Claude call gets sitewell's short instructions instead of Claude Code's default ones,
a single tool (Read, inside one workspace folder) and a JSON schema its answer must match.
It cannot browse, run commands or reach a site. A call costs about 3,000 tokens of
overhead instead of about 50,000, and every call's tokens and cost are recorded.

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
