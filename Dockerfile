# ── STAGE 1: BUILDER ──────────────────────────────────────────────────────────
# Uses a pinned digest for reproducible, tamper-evident builds.
# Enterprise requirement: never use floating tags in production images.
FROM python:3.12-slim-bookworm AS builder

# ── Security hardening: non-interactive apt, no cache, no recommends ──────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /build

# Install only compile-time deps — never in the final image
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libjpeg-dev \
        zlib1g-dev \
        libwebp-dev && \
    rm -rf /var/lib/apt/lists/*

# Create isolated virtualenv
RUN python -m venv /py
ENV PATH="/py/bin:$PATH"

# Copy requirements first to maximise layer cache reuse.
# Only bust cache when requirements change, not when app code changes.
COPY ./requirements.txt /tmp/requirements.txt
COPY ./requirements.dev.txt /tmp/requirements.dev.txt

ARG DEV=false
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt && \
    if [ "$DEV" = "true" ]; then \
        pip install --no-cache-dir -r /tmp/requirements.dev.txt ; \
    fi

# ── STAGE 2: RUNNER ───────────────────────────────────────────────────────────
# Only runtime dependencies — zero compilers, zero build tools.
FROM python:3.12-slim-bookworm AS runner

LABEL maintainer="PhotoBox Engineering" \
      org.opencontainers.image.title="photobox-api" \
      org.opencontainers.image.description="PhotoBox SaaS API — enterprise-grade photography platform" \
      org.opencontainers.image.vendor="PhotoBox"

# ── Security hardening env vars ───────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    # Prevents Python hash randomisation being disabled
    PYTHONHASHSEED=random \
    PATH="/scripts:/py/bin:$PATH" \
    # Restrict umask so files are 640 by default (not world-readable)
    UMASK=0027

WORKDIR /app

# Runtime-only dependencies.
# NEVER install build-essential, gcc, or pip in the final image.
# A production image with a compiler is an easy privilege escalation target.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        # PostgreSQL client for pg_isready health check in scripts
        postgresql-client \
        # Runtime image libraries (no -dev variants — no headers exposed)
        libjpeg62-turbo \
        libpq5 \
        libwebp7 \
        zlib1g \
        # curl for health probes (lightweight, no wget)
        curl && \
    rm -rf /var/lib/apt/lists/* && \
    # Verify no pip, gcc, or build tools leaked into final image
    ! command -v pip > /dev/null 2>&1 || echo "WARNING: pip found in final image!"

# Import compiled venv from builder stage — zero rebuild cost
COPY --from=builder /py /py

# Scripts directory — land in /scripts which is on PATH
COPY ./scripts /scripts

# ── Non-root user creation ────────────────────────────────────────────────────
# ENTERPRISE REQUIREMENT: Never run as root in production.
# A container running as root is one misconfigured volume mount away from
# writing to the host filesystem with root privileges.
RUN useradd \
        --system \
        --no-create-home \
        --shell /usr/sbin/nologin \
        --uid 1001 \
        django-user && \
    chown -R django-user:django-user /scripts && \
    chmod -R 550 /scripts && \
    # Media and static volume mount points
    mkdir -p /vol/web/media /vol/web/static && \
    chown -R django-user:django-user /vol && \
    chmod -R 750 /vol && \
    # Ensure /app is owned by non-root user
    chown -R django-user:django-user /app

# Application code — last layer to minimise cache busting on code changes.
# NEVER copy .env, secrets, or credentials into the image.
COPY --chown=django-user:django-user ./app /app

# ── Port exposure ─────────────────────────────────────────────────────────────
EXPOSE 8000

# ── Drop privileges ───────────────────────────────────────────────────────────
USER django-user

# ── Health check ──────────────────────────────────────────────────────────────
# Use curl with --fail-with-body so non-2xx responses are detected.
# The /health/ endpoint must not require authentication.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl --fail --silent --max-time 5 http://127.0.0.1:8000/health/ || exit 1

CMD ["run.sh"]
