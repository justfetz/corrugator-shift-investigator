# Use the local learning interface

Install and run from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e . pytest
.\.venv\Scripts\python -m investigator.server
```

Open http://127.0.0.1:8765/ on the same computer. The server is local only. No API key, model call or email service is involved.

1. Keep the default second shift and ask "Why was second shift slow? Chart downtime by reason."
2. Read the KPI cards and charts. Grade target markers use each wet-end run's grade.
3. Expand "How the agent got here" and a tool call. Inspect its arguments, exact SQL, bound values, returned rows, record references and time.
4. Ask "Compare that with first shift". The selected production day carries forward.
5. Enable "Include a bad trim record" and repeat. The affected metrics become incomplete, with the rejected source value visible in the trace.
6. Save a Markdown report. This downloads a local report; it does not send email.

The date selector covers 30 fictional days. Each day is generated deterministically on demand. Scenario patterns repeat with seeded quantity variation; this is not a calibrated statistical month or a trend-analysis tool yet.

To materialize and validate all XML days:

```powershell
.\.venv\Scripts\python -m investigator.archive
```

This writes 30 days / 90 shifts / 5,400 setups / 2,700 wet-end runs under ignored artifacts/month, with a file/hash/seed manifest. The archive matches the clean dataset in the day selector. The optional corrupt-record scenario is not included in the clean archive.

The offline planner is deliberately limited. Unsupported questions return a clear error. A real model adapter will replace the router, while retaining the tested tool boundary. See AGENT_CONTRACT.md for permissions and limits.


## Optional OpenAI mode

Select OpenAI in the mode control to use your own API account. See [OpenAI/BYOK setup and limits](OPENAI_BYOK.md). Real model access requires your key and incurs API charges; default offline use remains free.
