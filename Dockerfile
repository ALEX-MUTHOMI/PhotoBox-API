FROM python:3.12-slim-bookworm
LABEL maintainer="Back To Front Development"

# Force Python to not buffer stdout/stderr, ensuring logs stream instantly.
ENV PYTHONUNBUFFERED=1 \
    PATH="/scripts:/py/bin:$PATH"

# 1. DEPENDENCIES FIRST (The Cache Shield)
# We copy only the requirements and scripts first. 
# This guarantees pip install is CACHED unless you modify the requirements.txt file.
COPY ./requirements.txt /tmp/requirements.txt
COPY ./requirements.dev.txt /tmp/requirements.dev.txt
COPY ./scripts /scripts

# 2. THE HEAVY LIFT (System Setup & Package Installation)
ARG DEV=false
RUN python -m venv /py && \
    /py/bin/pip install --upgrade pip && \
    apt-get update && \
    # Install runtime dependencies (required for psycopg2 and Pillow)
    apt-get install -y --no-install-recommends postgresql-client libjpeg-dev && \
    # Install temporary build dependencies (compilers)
    apt-get install -y --no-install-recommends build-essential libpq-dev zlib1g-dev && \
    # Install Python packages
    /py/bin/pip install -r /tmp/requirements.txt && \
    if [ $DEV = "true" ]; \
    then /py/bin/pip install -r /tmp/requirements.dev.txt ; \
    fi && \
    # Cleanup: Purge build dependencies and nuke the apt cache to keep the image tiny
    apt-get purge -y --auto-remove build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf /tmp && \
    # Create the unprivileged user (Debian syntax)
    useradd --system --no-create-home django-user && \
    # Create media/static volume directories
    mkdir -p /vol/web/media && \
    mkdir -p /vol/web/static && \
    # Set strict ownership and execution permissions
    chown -R django-user:django-user /vol && \
    chmod -R 755 /vol && \
    chmod -R +x /scripts

# 3. COPY THE CODE LAST
# Now, when you edit views.py or models.py, Docker only rebuilds this specific layer.
COPY ./app /app
WORKDIR /app
EXPOSE 8000

# 4. DROP PRIVILEGES
USER django-user

CMD ["run.sh"]



