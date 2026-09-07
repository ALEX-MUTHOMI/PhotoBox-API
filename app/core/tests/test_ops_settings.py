"""Settings contracts for Celery results, statement_timeout, and IP_HASH_SALT."""
from django.conf import settings
from django.test import SimpleTestCase


class CeleryResultSettingsTests(SimpleTestCase):
    def test_celery_result_expires_one_hour(self):
        self.assertEqual(settings.CELERY_RESULT_EXPIRES, 3600)


class StatementTimeoutSettingsTests(SimpleTestCase):
    def test_runtime_statement_timeout_is_five_seconds_outside_migrate(self):
        # Test process is not migrate; OPTIONS may be absent on sqlite.
        engine = settings.DATABASES["default"]["ENGINE"]
        if "sqlite" in engine:
            self.skipTest("sqlite test DB has no statement_timeout OPTIONS")
        options = settings.DATABASES["default"].get("OPTIONS") or {}
        self.assertIn("statement_timeout=5000", options.get("options", ""))
