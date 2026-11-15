---
name: question-lab
description: Ask many questions in any controls (or the page's canned questions under every scope and period) through the offline path, then have the analyst agent read every card and return verdicts and golden-case candidates for the owner, ten at a time
---
The question lab of justfetz/corrugator-shift-investigator (issue #177).

1. **Run the lab.** It is offline: it calls no model and takes about a third of a second a card.
   - `python3 evals/lab.py --canned --scope both --period all --name canned` checks the page's canned questions under one shift and All, Day, Week, Month and 4-4-5.
   - `python3 evals/lab.py <file> --scope both --name <name>` runs the owner's questions. Use one question per line in a `.txt` file; `[scope=day period=week day=2026-09-16] question` sets one line's controls. A `.json` file takes `[{"question", "day", "shift", "scope", "period"}]`.
   - `--ask "<question>"` runs one question, and can be repeated.

   It writes `artifacts/lab/<name>.md` (for the owner) and `.json` (for the agent), under the golden store's window (2026-09-30).
2. **Hand the JSON to the `analyst` subagent** (`.claude/agents/analyst.md`). Tell it only the file's path and the controls you ran. Give it no view on which cards are wrong: it reads the cards fresh, against the domain docs it loads itself. It writes `artifacts/lab/<name>.review.md`.
3. **Show the owner:**
   - the tally;
   - every miss on a canned question first (the page's front door);
   - batch 1 of the golden-case candidates, ten at a time. `batch 2` is the next ten.

   Quote questions in code spans.
4. **For each candidate the owner approves,** add the case with `/add-golden-case`. Use the owner's words for what the card must lead with. A miss fails until its fix lands, which is the point. Each bug becomes a golden case before the fix (CLAUDE.md). The fix belongs to the issue that owns that behaviour; file one if none does.
5. **Never commit `artifacts/lab/`.** It is the lab's scratch, and it is gitignored. The goldens and the issues are what the repo keeps.
