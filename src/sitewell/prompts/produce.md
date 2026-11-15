Task: write the changes for the actions in decision.json, for {domain}. The site's language is Persian (fa-IR); write in the site's own voice.

Files:
- rules.md: the site's rules. They override everything below.
- decision.json: the actions to write, each with an instruction.
- pages/: the current content of every page involved, as the site serves it today (title, description, headings, text blocks, internal links, structured data). The file for a URL is named in pages/index.json.
- inventory.csv: all pages on the site. Link only to URLs listed there.
- facts.json: this week's facts, if you need a number.

Write one draft per action, with its action_id. Fill only the fields that action needs; leave every other field empty ("" or [], and the article and hand_task objects with empty strings and lists, minutes 0).
- summary: one short English line saying what the draft changes.
- new_title, new_meta_description: the exact new text the page will serve, nothing else (no labels, no explanations). Fill them only when the action changes them. Title at most 60 characters and description 120 to 155, unless rules.md says otherwise. Keep the doctor's name and the city where the current text has them.
- links: from_url (a page in pages/), to_url (a URL in inventory.csv), anchor (the linked words), sentence (the whole sentence as it will read, containing the anchor). Prefer an anchor that already appears on the from page.
- schema_jsonld: one complete JSON-LD object as a string, consistent with what the page already has.
- text_edits: section (the heading the text sits under), before (the exact current text, copied verbatim from pages/; empty when adding a new block), after (the new text). Change as little as possible: at most 2 edits and about 800 characters of new text per action. Prefer adding a short block to rewriting existing sentences, and never reword a medical sentence that is already on the page.
- article: only for a new page. A full draft in Markdown that answers the query in its first paragraph, uses H2 sections, adds an FAQ if the query invites one, and cites a source for every medical statement. Sources must be real, well-known medical references; if you can't source a statement, leave it out.
- hand_task: who does it, numbered steps, minutes it takes.
- notes: what you kept, and anything you could not do and why.

Never add a medical statement (a treatment, an outcome, a risk, a dose, a duration, a price) that the page does not already make, except in a new-page article, where each one is cited. Never use a word or claim rules.md forbids.
