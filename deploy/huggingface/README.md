---
title: Corrugator Shift Investigator
emoji: 🏭
colorFrom: gray
colorTo: green
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Ask a bounded agent why a corrugator shift was bad (synthetic data)
---

# Corrugator Shift Investigator — public synthetic demo

A manufacturing investigation agent you can question: speed, downtime and waste for thirty synthetic production days on one corrugator, with every number calculated by fixed DuckDB tools and the full tool trace shown under "How the agent got here".

This Space runs the **offline planner**: no model calls, no keys, no email. The synthetic records are generated deterministically on demand. Nothing here is a real plant.

Source, tests and the build-an-agent write-up: <https://github.com/justfetz/corrugator-shift-investigator>

Space settings this container expects (Settings → Variables):

| Variable | Value |
| --- | --- |
| `PUBLIC_DEMO_ORIGIN` | this Space's direct URL, e.g. `https://justfetz-corrugator-shift-investigator.hf.space` |
| `PUBLIC_DEMO_EMBED_ORIGINS` | `https://huggingface.co,https://justfetz.github.io` (the Space page itself frames the app) |
