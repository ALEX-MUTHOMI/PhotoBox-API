FROM python:3.12-slim-bookworm
LABEL maintainer="Back To Front Development"

ENV PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/py \
    POETRY_VERSION=2.4.1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1 \
    PATH="/scripts:/py/bin:$PATH"

ARG DEV=false
ARG INSTALL_DEPLOY=true
ARG APP_UID=1000
ARG APP_GID=1000

# Dependency metadata is copied before source so Docker can cache lockfile installs.
COPY ./pyproject.toml ./poetry.lock /tmp/poetry/
COPY ./scripts /scripts

RUN python -m venv /py && \
    python -m pip install --upgrade pip && \
    /py/bin/pip install --upgrade pip && \
    apt-get update && \
    apt-get install -y --no-install-recommends postgresql-client libjpeg-dev && \
    apt-get install -y --no-install-recommends build-essential libpq-dev zlib1g-dev && \
    python -m pip install "poetry==${POETRY_VERSION}" && \
    cd /tmp/poetry && \
    if [ "$DEV" = "true" ]; then \
      poetry install --with dev,test,lint,security --without deploy --no-root; \
    elif [ "$INSTALL_DEPLOY" = "true" ]; then \
      poetry install --with deploy --without dev,test,lint,security --no-root; \
    else \
      poetry install --without dev,test,lint,security,deploy --no-root; \
    fi && \
    python -m pip uninstall -y poetry poetry-core && \
    apt-get purge -y --auto-remove build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/* /tmp && \
    groupadd --gid ${APP_GID} django-user && \
    useradd --uid ${APP_UID} --gid ${APP_GID} --create-home --shell /usr/sbin/nologin django-user && \
    mkdir -p /vol/web/media /vol/web/static /home/django-user && \
    chown -R django-user:django-user /vol /home/django-user && \
    chmod -R 755 /vol && \
    chmod -R +x /scripts

COPY ./app /app
RUN chown -R django-user:django-user /app

WORKDIR /app
EXPOSE 8000
ENV HOME="/home/django-user"

USER django-user

CMD ["run.sh"]
