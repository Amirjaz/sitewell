Task: check each draft in drafts.json against rules.md and the page's current content in pages/ (the file for a URL is named in pages/index.json). You report problems; you don't fix them.

For each draft, by action_id:
- new_medical_claims: every sentence in the new text that states a treatment, outcome, risk, dose, duration or price which the current page does not already state. Quote the sentence. Empty when there is none.
- language_issues: spelling mistakes, a missing or wrong half-space (نیم‌فاصله), Arabic ي or ك instead of Persian ی and ک, mixed Persian and Latin digits in one number, broken sentences. Quote each one.
- overlaps: URLs from inventory.csv whose main topic or main query the new text duplicates. Empty when there is none.
- breaks_never_rules: every real breach of a rule listed under "## Never" in rules.md, naming the rule. Leave the list empty when there is none; never write "none" or "no breach" into it.
- needs_doctor: everything in the draft that rules.md lists under "## Needs the doctor" (for example medical wording), naming the rule. Empty when there is none.
