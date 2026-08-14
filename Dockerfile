# Bulletin worker — containerized local_worker.sh
#
# Runs main.py on a cron schedule inside the container. Exists because a few
# parish sites block GitHub Actions' datacenter IPs but load fine from a
# residential connection, so those parishes have to be processed from home.
#
# The image holds dependencies and the worker scripts, NOT the application
# code: /app is cloned from GitHub at container start and re-synced before
# every scheduled run (docker/sync-code.sh). Shipping a code fix therefore
# means pushing to main, not rebuilding this image. Rebuild only when the
# base image, the worker scripts, or the dependency baseline should change.
#
# Build:  docker build -t bulletin-worker .
# Run:    see docker-compose.yml or docker/README.md

FROM python:3.12-slim

# cron   — the scheduler (PID 1; its stdout is the container log)
# git    — how /app gets its code, and how it stays current
# tzdata — so TZ=America/New_York makes CRON_SCHEDULE local time
# util-linux — flock, to keep a slow run from stacking on the next one
# ca-certificates — TLS for GitHub / OpenAI / Notion / parish sites
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        cron \
        git \
        tzdata \
        util-linux \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Dependency baseline. Installing at build time means a cold container is ready
# immediately; sync-code.sh reinstalls at run time only when the requirements.txt
# it pulls differs from what this layer put in. The hash marker is what tells it.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && mkdir -p /var/lib/bulletin-worker \
    && sha256sum /tmp/requirements.txt | cut -d' ' -f1 > /var/lib/bulletin-worker/requirements.sha256 \
    && rm /tmp/requirements.txt

COPY docker/entrypoint.sh docker/run-worker.sh docker/sync-code.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/run-worker.sh /usr/local/bin/sync-code.sh

WORKDIR /app

# /logs — per-run logs; mount a host path to keep them across recreates.
# /app  — the checkout. Left as a plain directory on purpose: a fresh clone each
#         time the container is created is cheap and unambiguous. Mount a host
#         path here if you would rather it persist.
VOLUME ["/logs"]

ENV APP_DIR=/app \
    LOG_DIR=/logs \
    REPO_URL=https://github.com/mfgarvin/bulletin.git \
    BRANCH=main \
    AUTO_UPDATE=true \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
