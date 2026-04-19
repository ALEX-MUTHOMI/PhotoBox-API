"""
app/celery.py — Celery Application Bootstrap

This is the entry point for the Celery worker processes.
It autodiscovers tasks from all INSTALLED_APPS (gallery.tasks, etc.)
and configures the broker and result backend from Django settings.

Usage:
    celery -A app worker --loglevel=info
    celery -A app beat --loglevel=info  (for scheduled purge tasks)
"""
import os
from celery import Celery

# Set the default Django settings module for the 'celery' CLI
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'app.settings')

app = Celery('photobox')

# Pull Celery config from Django settings, using the CELERY_ namespace prefix
app.config_from_object('django.conf:settings', namespace='CELERY')

# Autodiscover tasks from all installed apps that define tasks.py
# This finds: gallery.tasks.process_fast_lane_asset, gallery.notifications.send_gallery_ready_email, etc.
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Diagnostic task for verifying the Celery broker connection."""
    print(f'Request: {self.request!r}')
