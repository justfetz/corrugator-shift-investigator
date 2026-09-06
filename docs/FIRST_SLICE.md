# Run the deterministic first slice

This implementation generates one fixed synthetic production day, imports it into a private in-memory DuckDB database and executes analysis tools. The language-model loop, interactive website and email delivery are not implemented yet.

From the repository root, with Python 3.11+:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e . pytest
$env:PYTHONPATH = "src"
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m investigator
```

The CLI writes ignored files to artifacts/: production.xml, tool-results.json and one downtime SVG per shift. Open the JSON to inspect SQL, bound parameters, result rows, record references, dataset hash and timing. Open an SVG to see those downtime values rendered by trusted code. No API key or network call is used by the demo.

## Implemented

- Reproducible seeded generation: three shifts, 180 dry-end setups, 90 wet-end IDs and two knife assignments per setup.
- Shared knife lineal travel, repeated order IDs, centered web positions and gross/shear/trim/reject reconciliation.
- UTF-8 XML intake with DTD/entity, size, depth and element-count restrictions.
- Invalid setup values preserved in exclusions, with affected results labeled incomplete.
- get_shift_kpis and get_downtime_breakdown with parameter binding and grouping allowlists.
- Trusted static downtime SVGs from returned rows.

## Explicit simplifications and remaining work

- Fixed local demonstration time for September 1, 2026. No timezone/DST handling. Cross-shift records and overlapping stops are rejected rather than allocated; midnight within third shift is supported.
- Two setups per wet-end ID, exact aligned integer knife cuts, six shear chops per setup and two total inches of trim are synthetic fixture choices, not plant rules. Shear is charged full width; side trim applies only to remaining footage. Dry-end rejects refer to individual upper-knife sheets after trimming.
- Recipes, bales, direct-area reject entry and full customer-demand fulfillment are not implemented.
- A corrupt setup excludes the whole setup from production aggregates. Observed speed is an incomplete observed contribution, not an estimate of true full-shift speed; it must not be ranked as valid performance. Downtime inherits conservative shift-wide coverage warnings even when its own records are sound.
- Physical zero speed during stops is implicit: generator uses remaining running seconds to generate footage. No instantaneous speed trace is generated.
- Full wet-end target comparisons, alarms, richer schemas and a persistent evidence store remain to build. Tool scope uses the one loaded production day.
- The database is private/in-memory with external access disabled after ingestion. Only fixed SELECT methods are exposed; this is not a production sandbox. Hard query timeouts, session budgets and typed error envelopes must precede public model access.
- Model loop, follow-up context, result-ID chart tool and interactive execution panel remain in issue #3. Static SVG output is the first chart proof.

## Validation

Tests cover known downtime totals, independently calculated area/speed, continuing orders, shared lineal, corrupt values, unsafe XML, invalid arguments, overlapping stops, bad knife footage, overnight shift and escaped chart labels.
