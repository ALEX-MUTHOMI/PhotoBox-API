"""
app/celery.py — Celery Application Bootstrap

This is the entry point for the Celery worker processes.
It autodiscovers tasks from all INSTALLED_APPS (gallery.tasks, etc.)
and configures the broker and result backend from Django settings.
"""
import os
from celery import Celery

# 1. Set the default Django settings module for the 'celery' CLI
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'app.settings')

# 2. MUST match your core Django folder name ('app'), NOT your brand name.
app = Celery('app')

# 3. Pull Celery config from Django settings, using the CELERY_ namespace prefix
app.config_from_object('django.conf:settings', namespace='CELERY')

# 4. Autodiscover tasks from all installed apps that define tasks.py
app.autodiscover_tasks()

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Diagnostic task for verifying the Celery broker connection."""
    print(f'Request: {self.request!r}')