#!/bin/sh

set -e

# 1. Boot sequence
echo "[INFO] Waiting for database..."
python manage.py wait_for_db
echo "[INFO] Applying migrations..."
python manage.py migrate
echo "[INFO] Collecting static files..."
python manage.py collectstatic --noinput

# 2. Start uWSGI
# We use --http :8000 so the container is reachable by the health check 
# and the external ports. In production, Nginx will talk to this.
echo "[INFO] Starting uWSGI in development mode..."
exec uwsgi --master \
           --http :8000 \
           --workers 4 \
           --enable-threads \
           --module app.wsgi \
           --buffer-size 32768