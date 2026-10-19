# The dataset

Thirty synthetic production days, 2026-09-01 to 2026-09-30, for one corrugator. Every
number the investigator shows is computed from these files and nothing else.

| File | What it is |
| --- | --- |
| `2026-09-DD.xml` | One production day: three shifts (07:00–15:00, 15:00–23:00, 23:00–07:00), each a timeline of `setup` records (with their `knife` orders) and `stop` records. This is the demo schema the app ingests; it does not claim any OEM protocol. |
| `manifest.json` | Seed, SHA-256, setup and wet-end-run counts per day. The app regenerates each day from `seed` at request time and the bytes are identical; `tests/test_data_archive.py` checks the hashes. |
| `csv/setups.csv` | 5,432 rows. One dry-end setup: wet-end run, grade and target speed, elapsed seconds, lineal feet, gross / trim / shear / rejected square feet, reject reason, width. |
| `csv/stops.csv` | 440 rows. One recorded stop: kind (Maintenance / Operator), place, reason, seconds, note. Reasons are reported symptoms, not diagnoses. |
| `csv/orders.csv` | 10,354 rows. One knife of one setup: order id, sheet width and length, outs, cuts, requested / planned / produced sheets, remaining demand before and after. Repeated orders are fragments of one order; never sum repeated demand. |
| `csv/shifts.csv` | 90 rows. Scheduled seconds and the shift note. |

Each CSV carries a `production_day` column so the whole month loads as one table.

## Rerun the investigator's SQL yourself

The app's tools are parameterized DuckDB queries over four in-memory tables with these
same columns. The trace panel in the app shows the exact SQL and the bound parameters
for every tool call. To run the same arithmetic without the app:

```bash
pip install duckdb
python3 tools/verify_day.py 2026-09-14 2
```

That script loads the CSVs with plain `read_csv_auto`, computes every KPI from the
formulas in the README (`sum(feet) / 480`, `sum(trim) / sum(gross) * 100`, …), and prints
them next to the app's tool output. They agree to four decimals for every day and shift.

Or open a DuckDB shell:

```sql
SELECT reason, sum(seconds)/60 AS down_minutes
FROM read_csv_auto('data/csv/stops.csv')
WHERE production_day = '2026-09-14' AND shift = 2
GROUP BY reason ORDER BY down_minutes DESC;
```

## Regenerate

```bash
python3 -m investigator.archive --output data
```

Deterministic: same seeds, same bytes, same hashes. Change the generator and the test
that pins the hashes fails, which is the point.

## What it is not

Not a real plant. Grade targets, widths, stop frequencies and the run-size distribution
are simulation settings supplied by code, calibrated with one domain expert, documented
in `docs/METRICS.md`. Notes are synthetic text. Nothing here establishes a root cause,
a cost, or a recoverable gain.
