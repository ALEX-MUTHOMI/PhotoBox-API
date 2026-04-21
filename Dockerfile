# ── STAGE 1: BUILDER ──────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /build
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libjpeg-dev \
    zlib1g-dev && \
    rm -rf /var/lib/apt/lists/*
RUN python -m venv /py
ENV PATH="/py/bin:$PATH"
COPY ./requirements.txt /tmp/requirements.txt
COPY ./requirements.dev.txt /tmp/requirements.dev.txt
ARG DEV=false
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt && \
    if [ "$DEV" = "true" ]; then \
    pip install --no-cache-dir -r /tmp/requirements.dev.txt ; \
    fi

# ── STAGE 2: RUNNER ───────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm
LABEL maintainer="Back To Front Development"
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/scripts:/py/bin:$PATH"
WORKDIR /app
# Runtime dependencies only — no compilers
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    postgresql-client \
    libjpeg62-turbo \
    libpq5 \
    zlib1g && \
    rm -rf /var/lib/apt/lists/*
# Import compiled venv from builder — no technical debt
COPY --from=builder /py /py
# Scripts land in /scripts which is on PATH
COPY ./scripts /scripts
RUN chmod -R +x /scripts && \
    useradd --system --no-create-home django-user && \
    mkdir -p /vol/web/media /vol/web/static && \
    chown -R django-user:django-user /vol && \
    chmod -R 755 /vol
# Application code — last so code changes don't bust dependency cache
COPY ./app /app
EXPOSE 8000
USER django-user
CMD ["run.sh"]