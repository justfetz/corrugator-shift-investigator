# Hosted synthetic demo of the Shift Investigator (offline planner by default).
# Listens on 0.0.0.0:$PORT (7860 by default, the Hugging Face Spaces convention).
# Required env: PUBLIC_DEMO_ORIGIN (the site's own https origin). Optional:
# PUBLIC_DEMO_EMBED_ORIGINS, PUBLIC_DEMO_LIVE_MODEL=1 + ANTHROPIC_API_KEY and/or
# OPENAI_API_KEY (server keys only; Cloud Run mounts them from Secret Manager by name).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=7860
RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
# The golden set, its recorded fixtures and the tracked history, read-only, for the /evals page (issue #26).
COPY evals ./evals
# The Markdown behind /docs (issue #93), rendered once below; the folder's design mockups are not copied.
COPY docs/*.md ./docs/
# Generate the two synthetic months once here, not at every container start (issue #19):
# the server loads /app/prebuilt in about 150 ms instead of generating for about 2 s.
RUN pip install --no-cache-dir . && python -m investigator.prebuilt /app/prebuilt \
    && python -m investigator.docsite build /app/docs-site --root /app \
    && mkdir -p /app/artifacts && chown -R app:app /app
ENV INVESTIGATOR_PREBUILT=/app/prebuilt INVESTIGATOR_EVALS=/app/evals INVESTIGATOR_DOCS=/app/docs-site

USER app
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"7860\")}/api/health').read()" || exit 1
CMD ["python", "-m", "investigator.server"]
