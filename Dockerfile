# ─────────────────────────────────────────────────────────────
#  MY Market Radar · single-container image
#  Stage 1 builds the React frontend; stage 2 runs FastAPI + Chromium CDP.
# ─────────────────────────────────────────────────────────────
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim

ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG NO_PROXY=""

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/backend \
    BROWSER_EXECUTABLE=/usr/bin/chromium \
    BROWSER_HEADLESS_FALLBACK=true

WORKDIR /app

# The collector always needs a Chromium-compatible browser, even before a captcha appears.
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/backend/requirements.txt

COPY backend/ /app/backend/
COPY --from=frontend-build /app/frontend/dist/ /app/frontend/dist/

RUN mkdir -p /app/backend/data /app/backend/logs

WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
