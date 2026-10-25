# syntax=docker/dockerfile:1
# Server image: FastAPI + the built web app. It never transcribes (WORKER_MODE=remote):
# the Mac worker does that. Built and run by deploy/docker-compose.yml.

# ---- 1. the front end ---------------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- 2. the app ---------------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CALLBENCH_KEYWORDS=/app/config/keywords.txt

# ffprobe reads duration/channels at upload; sqlite3 is for deploy/backup.sh
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg sqlite3 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-server.txt .
RUN pip install -r requirements-server.txt

COPY api/ api/
COPY transcribers/ transcribers/
COPY scoring/ scoring/
COPY pipeline.py .
# data/ is a volume at runtime (DB + uploads), so the keyword list lives elsewhere
COPY data/keywords.txt config/keywords.txt
COPY --from=web /web/dist web/dist

RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin app \
 && mkdir -p data backups \
 && chown app:app data backups
USER app

EXPOSE 8000
# One worker process: the box has 4 GB and scoring is an API call. --proxy-headers trusts
# X-Forwarded-For only from FORWARDED_ALLOW_IPS (Caddy's fixed address, set in compose),
# so the sign-in lockout sees real client IPs.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
