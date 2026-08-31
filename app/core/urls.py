"""URL mappings for the core app. Namespaced under 'core:'."""
from django.urls import path

from core import views

app_name = "core"

urlpatterns = [
    path("resolve-domain/", views.resolve_domain, name="resolve-domain"),
]
