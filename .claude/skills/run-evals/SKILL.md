---
name: run-evals
description: Run the golden eval set, offline or live with a provider key, and report the score table and latency
---
1. Run `python3 evals/run.py` (offline, about 21 s). Paste the final score table.
2. If asked for live: `python3 evals/run.py --live --provider anthropic` (needs `ANTHROPIC_API_KEY`; records fixtures under `evals/fixtures/`). Report the cost per case.
3. Compare with the last line of `evals/history.jsonl`. Report every case that changed state, and p50 and p95 latency when the run records them.
4. Never delete or weaken a case to make the run pass. Open an issue instead and say why.
