# Data

Every record the investigator reads is synthetic. One generator, one seed and one machine produce the production days the rail offers; three stories are planted in them for the tools to find. Nothing comes from a real plant, and nothing reaches the store at run time.

## What is in the store

- **One machine.** `CORR-1`, a 110-inch corrugator (`/corr/1/…` in an address).
- **A rolling year.** Every Monday to Friday from 365 days before the window's last calendar day (`as_of`) to that day: about 262 production days of three 480-minute shifts. On the hosted demo `as_of` is the day before the image was built, so the window ends yesterday and moves forward every weekday night; tests and the golden set pin it to 30 September 2026 (`INVESTIGATOR_AS_OF`), the year from 30 September 2025. A production day runs 07:00 to 07:00 and is labeled by its starting date; weekends are plant-down and emit no records, and there is no holiday calendar. `/api/health` reports the served window as `store.window` (`as_of`, `first_day`, `last_day`, `production_days`), and the page footer names it.
- **Four record families.** Dry-end setups (with their customer blocks and reject lines), wet ends, shifts and stops, whose field names and units mirror a corrugator controller's own record types. [The record families](RECORDS.md) defines every field.
- **One seed, one day at a time.** `synth.generate_day(day, seed=2026)` draws everything for a day from streams keyed by the seed and its date (the day itself, its production week's speed factor, its month's power outage), so a day is byte-identical in any window that holds it, and moving the window never changes a day it keeps. `generate_window(as_of)` concatenates the year; a content hash covers it. The hash's first twelve characters are the `dataset_hash` of `/api/health`; value goldens and recordings are stamped with it, so a generator change cannot slip past the golden set.
- **A derived table.** `shift_kpis` holds one row of summed quantities per shift, built with the store, so a month report is a lookup rather than a scan ([metrics](METRICS.md#the-shift_kpis-table)).
- **A corrupt record on request.** *Include a bad trim record* under *Agent and advanced* injects one invalid setup into shift 2 of the selected day, to show a record flagged, excluded and reported instead of averaged in.

## The invariants

The generator's output satisfies physics and accounting rules, each pinned by a test over every record of August and September 2026 (and the per-day determinism over windows that share days): gross area is lineal feet times web width; both knives share the machine's travel; wet-end and shift totals are exact sums of their setups; setups tile each shift with no gap; stops sit inside one setup and never overlap. The monthly bands it keeps (downtime, trim, shear and dry-end waste) are simulation choices, listed in [the record families](RECORDS.md#generator-behaviour-and-the-bands-it-keeps), not plant standards.

## The three planted stories

The answer keys are in `src/investigator/stories.py`, which only the generator imports; no tool, planner, server or report can read them (a test pins that), so an investigation has to find them in the records.

1. **Trim creep after a rebuild.** A slitter/scorer rebuild in the Thursday 20 August maintenance window; afterwards side trim creeps about 0.1 percentage point a week until 31 October 2026, then returns to its pre-story level, and knife and slitter jam stops rise, concentrating on the slitter/scorer.
2. **Missed splices on Splicer 3.** In the week of 7–13 September missed-splice minutes roughly triple, stop counts rise with them, and shear chops follow.
3. **Monday warp.** Warp rejects run about 1.5 times higher on the first production day after a weekend, and higher in August than in September.

The stories keep their 2026 dates in every window, so a window that starts after them simply holds none of them.

The investigation cases of the golden set ask about each story in a superintendent's words, and pass only when the answer finds the evidence and asserts no cause the records lack ([numbers and evals](EVALS.md)).

## The question log

The one thing the hosted demo writes about its visitors (issue #125): what they ask, so a question the router misses becomes a golden case. The page footer says so ("Questions are logged anonymously to improve the answers; the question log keeps no IP address, browser details or cookies"). Each line is one JSON object on stdout, which Cloud Run ships to Cloud Logging; a log sink copies them to BigQuery ([deployment](DEPLOYMENT.md#the-question-log)), and the `/review-questions` skill turns a week of them into candidate golden cases. The lines of one question share its `line` id, a random value the page draws when the question is asked; `session` is a random value per browser tab, kept in sessionStorage, that groups a visitor's follow-ups. Both are 16 lowercase hex characters or are dropped. A reloaded card is not asked again and writes nothing.

Every line carries `log` (`question_log`), `v` (the schema, 1), `event`, `ts` (UTC), `line`, `session`, `version`, `revision` (Cloud Run's revision name, so a preview's lines say so) and `dataset` (the first twelve characters of the dataset hash), and, for Cloud Logging, `severity` and `message`. Then, by event:

| Event | Written when | Fields |
| --- | --- | --- |
| `question` | `/q` routed the question, or refused it | `question` (at most 500 characters, with the replacements below), `truncated`, `scope` (the rail's `day`, `shift`, `scope`, `period`, `corrupt`), `follow_ups` (how many prior views the question followed; the views themselves are not kept), `url` (the canonical address without its `after=` chain and without the visitor's `rates=`), `measure`, `status` (`routed` or `refused`), `refusal`, `server_ms` (the routing), `flags` |
| `answer` | that address answered it (the page follows `/q`'s redirect) | `url`, `measure`, `status` (`complete`, `partial`, `incomplete`, or `refused`, `limited`, `failed`), `refusal`, `server_ms` (the answer) |
| `analyst` | an "Ask the analyst" round on it ended, or was refused | `question`, `truncated`, `scope`, `mode` (the provider), `status` (the round's: `complete`, `plan_rejected`, `unavailable`, `partial`, …), `narrative`, `model_calls`, `tool_calls`, `refusal`, `server_ms`, `flags` |
| `feedback` | a thumbs up or down on its card, and the optional comment | `card` (`answer`, or `analyst` for the analyst's card), `vote` (`up` or `down`), `comment` (at most 300 characters of plain text, the same replacements as a question; a comment with markup, a line break, a format character or a character of the tag block U+E0000–E0FFF is refused, while the other invisible characters, such as an emoji's variation selector U+FE0F, are removed from it; the markup and line-break checks read the comment with those removed, so one cannot hide a link or an entity from the check) |

The flags are computed when the line is written: `refused` (the router refused it), `catch_all` (it landed on `measure=kpis` while it both asks for a quantity, such as "how many", "number of", "how much" or "what was", and names a countable noun or a unit, such as setups, stops, rolls, minutes, feet, MSF, percent or dollars: the #113 guard), `typo_corrected` (a misspelt measure word was read as the measure), `scope_moved` (its words moved the shift or the window, issue #105) and, on an analyst line, `analyst_plan_rejected`.

**What is replaced in a question or a comment** before the line is written: invisible characters are removed, which a reader cannot see and a model still reads: the format characters (Unicode category Cf: bidi overrides, zero-width characters, the byte-order mark, the tag characters) and the other default-ignorable code points (the variation selectors, the combining grapheme joiner, the Hangul fillers and the whole tag block U+E0000–E0FFF); email addresses become `[email]`; IPv4 and IPv6 addresses (full, compressed, with a port in brackets, with a zone, IPv4-mapped) become `[ip]`; nine or more digits in one run with spaces, dots, dashes, slashes or brackets between them (phone, card, account and social-security numbers), and a 3-4 local number such as 123-4567, become `[number]`, while an ISO date (2026-09-14) is kept, and in a run that holds one the digits beside the date are read on their own, whatever separator touches the date (`on 2026-09-14 020 7946 0958` and `on 2026-09-14-555-1234` keep the date and lose the phone number); and key-like tokens become `[key]`: a run of 20 or more letters, digits, `_` and `-` that holds both a letter and a digit, or a run of 20 or more that holds `/` or `+` together with a lower-case letter, an upper-case letter and a digit, as an AWS secret has. A lower-case path such as `/corr/1/week/2026-09-07` and a figure such as 13,233.4 MSF are kept. At most 30 lines a minute are written for one client address; the answers are never held back, and the address is used only to count.

**Cloud Run's own request log is a different log.** Like every web server's access log, it records each request's URL with the visitor's IP address and user agent, and the URL of `/q` holds the question. Cloud Logging keeps that log for 30 days; it is not copied to BigQuery, and nothing joins it to the question log. [Deployment](DEPLOYMENT.md#the-question-log) has an optional exclusion, for the owner to run, that drops the `/q` entries from it.

What a line never holds: an IP address, a user agent, a cookie, a key or any other request header's value, the follow-up chain, the visitor's own rates, a model's prompt or narrative, an evidence row. Cloud Logging keeps the lines 30 days; the BigQuery copy keeps each day 90 days.

## When the data is built

The image generates the store once, at build time: `python -m investigator.prebuilt /app/prebuilt` builds the year ending yesterday (about 15 s), and `--as-of 2026-09-30 /app/prebuilt-golden` builds the golden year the `/evals` page scores against. The server loads the first folder at start-up in about a quarter of a second instead of generating for about 12 s; the loopback workbench generates when the server starts (`INVESTIGATOR_AS_OF` pins its window, otherwise it ends yesterday). The data never changes while a server runs: there is no upload, no import and no job that adds days.

## The nightly window

A "daily load" is the window moving by one day, and nothing is loaded: the Cloud Run deploy also runs every weekday at 13:00 UTC (07:00 or 08:00 in Chicago, once the overnight shift has ended) and rebuilds the image, so the new day is generated from its date and the oldest drops off ([deployment](DEPLOYMENT.md), "The nightly deploy"). Its smoke test fails the deploy unless `/api/health` reports a window that ends yesterday, so an image that froze an older window cannot pass silently. A window reaching before the year's first day or after its last is unavailable with the tool's reason, never zero, and a month named without a year means the latest such month on or before the selected day. A year period (`period=year`) is not built yet (issue #106).
