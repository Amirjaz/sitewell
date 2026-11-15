Task: choose this week's SEO actions for {domain}.

Read these files before deciding:
- rules.md: the site's rules. They override everything below. Never propose what they forbid.
- guard.json: what the code has already settled: pages locked by the 14-day rule, the new-page budget, the queries that are evidence for a new page, and whether changes are paused and why.
- facts.json: this week's computed facts (Search Console, detectors, baseline, Google updates, the page inventory summary, rivals, and past changes judged).
- inventory.csv: every page on the site with its title, headings, word count, structured data, issues and last 28 days of clicks and impressions.
- ledger.json: changes already made to the site and, where judged, how they did.
- previous.json: what you proposed in earlier weeks. This is shadow mode, so none of it was shipped; don't repeat it word for word without saying so.

How to choose:
1. At most 5 actions, ranked by expected clicks. Fewer is fine. None is fine when nothing is worth doing.
2. Prefer improving pages that already rank over new pages: a title or description for a click-through gap, a section that answers a striking-distance query on the page that should own it, internal links toward that page, structured data.
3. A new page only for a query in guard.json new_page_evidence, only when guard.json says the budget allows one, and never for a query rules.md forbids targeting (for example superlative searches).
4. Never act on a page in guard.json locked_pages.
5. Every action cites its evidence: the facts.json section and its numbers (query, position, impressions, clicks). "expected" is a calculation from those numbers, such as impressions x (target CTR - current CTR), written out; or "not estimable" with the reason.
6. "url" is a full URL from inventory.csv. For a new page, the proposed full URL. "query" is the search query the action serves, or "" if none.
7. "instruction" tells a separate writer exactly what to change and what to keep, in plain English, so the writer can do it from the page alone.
8. If guard.json says changes are paused, still choose the actions (they will be held) and set pause.recommended to true with the reasons.
9. Work a person must do (a directory profile, a photo, the doctor's answer) is type "hand-task". At most 3, and only when it clearly matters more than what you can do yourself.
10. not_doing: the obvious moves you rejected and why (a rule, the 14-day lock, too little evidence, a Google update).
11. week_summary: 3 to 5 plain sentences on what the data says, with numbers. plan_revision: what should change in the site's longer-term plan, or "" if nothing.
12. IDs are a1, a2, a3 and so on.
