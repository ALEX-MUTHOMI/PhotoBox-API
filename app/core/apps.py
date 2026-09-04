"""AppConfig: DRF / Spectacular compatibility shims."""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        import core.signals  # noqa: F401

        # drf-spectacular<=0.22 references serializers.NullBooleanField, removed in DRF 3.14+.
        import rest_framework.serializers as drf_serializers

        if not hasattr(drf_serializers, "NullBooleanField"):
            drf_serializers.NullBooleanField = drf_serializers.BooleanField  # type: ignore[attr-defined]
