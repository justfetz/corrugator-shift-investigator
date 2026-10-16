# Hosted synthetic demo of the Shift Investigator (offline planner by default).
# Listens on 0.0.0.0:$PORT (7860 by default, the Hugging Face Spaces convention).
# Required env: PUBLIC_DEMO_ORIGIN (the site's own https origin). Optional:
# PUBLIC_DEMO_EMBED_ORIGINS, PUBLIC_DEMO_LIVE_MODEL=1 + OPENAI_API_KEY (server key only).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=7860
RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && mkdir -p /app/artifacts && chown -R app:app /app

USER app
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"7860\")}/healthz').read()" || exit 1
CMD ["python", "-m", "investigator.server"]
