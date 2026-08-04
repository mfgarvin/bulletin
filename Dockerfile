# Bulletin worker — containerized local_worker.sh
#
# Runs main.py on a cron schedule inside the container. Exists because a few
# parish sites block GitHub Actions' datacenter IPs but load fine from a
# residential connection, so those parishes have to be processed from home.
#
# Build:  docker build -t bulletin-worker .
# Run:    see docker-compose.yml or docker/README.md

FROM python:3.12-slim

# cron   — the scheduler (PID 1; its stdout is the container log)
# tzdata — so TZ=America/New_York makes CRON_SCHEDULE local time
# util-linux — flock, to keep a slow run from stacking on the next one
# ca-certificates — TLS for OpenAI / Notion / parish sites
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        cron \
        tzdata \
        util-linux \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so code edits don't bust the pip layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code. .dockerignore keeps venv/, logs, PDFs and secrets out.
COPY . .

COPY docker/entrypoint.sh docker/run-worker.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/run-worker.sh

# Per-run logs land here; mount a host path to keep them across recreates.
VOLUME ["/logs"]

ENV LOG_DIR=/logs \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
