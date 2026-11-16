---
name: question-lab
description: Ask many questions in any controls (or the page's canned questions under every scope and period) through the offline path, then have the analyst agent read every card and return verdicts and golden-case candidates for the owner, ten at a time
---
The question lab of justfetz/corrugator-shift-investigator (issue #177).

1. **Run the lab.** It is offline: it calls no model and takes about a third of a second a card.
   - `python3 evals/lab.py --canned --scope both --period all --name canned` checks the page's canned questions under one shift and All, Day, Week, Month and 4-4-5.
   - `python3 evals/lab.py <file> --scope both --name <name>` runs the owner's questions. Use one question per line in a `.txt` file; `[scope=day period=week day=2026-09-16] question` sets one line's controls. A `.json` file takes `[{"question", "day", "shift", "scope", "period"}]`. The controls are `day` (a production day of the golden store), `shift` (1, 2 or 3), `scope` (`shift` or `day`, which is All) and `period` (`day`, `week`, `month` or `accounting`). A typo in one (`[perod=week]`, `[scope=all]`, `[period=fiscal]`) stops the lab with `lab error:` and the allowed keys and values, before any question is asked: fix the line and run it again.
   - `--ask "<question>"` runs one question, and can be repeated; it takes a line's controls too (`--ask "[scope=day] how many setups"`).

   It writes `artifacts/lab/<name>.md` (for the owner) and `.json` (for the agent), under the golden store's window (2026-09-30).
2. **Hand the JSON to the `analyst` subagent** (`.claude/agents/analyst.md`). Tell it only the file's path and the controls you ran. Give it no view on which cards are wrong: it reads the cards fresh, against the domain docs it loads itself. Its tools are Read, Grep and Glob, so it runs and edits nothing: its review is its final message.
   - **Re-asks.** If the review lists re-asks, write them, one lab line each, to `artifacts/lab/<name>-reask.txt`, run `python3 evals/lab.py artifacts/lab/<name>-reask.txt --name <name>-reask`, and send the agent the new JSON's path (SendMessage to the same agent) so it finishes those verdicts. Run only the lines it gave.
   - **Candidates.** Every golden-case candidate must carry `"reviewed_by": "owner-pending"`. Send any that lacks it back to the agent; never add the field yourself to a candidate the owner will read as the agent's.
   - **Write the review.** Write the agent's final message, as it wrote it, to `artifacts/lab/<name>.review.md`.
3. **Show the owner:**
   - the tally;
   - every miss on a canned question first (the page's front door);
   - batch 1 of the golden-case candidates, ten at a time. `batch 2` is the next ten.

   Quote questions in code spans.
4. **For each candidate the owner approves,** add the case with `/add-golden-case`, keeping `"reviewed_by": "owner-pending"`: only the owner marks a case `owner`. Use the owner's words for what the card must lead with. A miss fails until its fix lands, which is the point. Each bug becomes a golden case before the fix (CLAUDE.md). The fix belongs to the issue that owns that behaviour; file one if none does.
5. **Never commit `artifacts/lab/`.** It is the lab's scratch, and it is gitignored. The goldens and the issues are what the repo keeps.
