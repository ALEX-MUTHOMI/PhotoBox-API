import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase


APP_DIR = Path(__file__).resolve().parents[2]


class SettingsBootTests(SimpleTestCase):
    def _run_manage_check(self, debug_value: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "TESTING": "1",
                "DEBUG": debug_value,
                "DB_NAME": "",
                "DB_HOST": "",
                "DB_USER": "",
                "DB_PASS": "",
            }
        )
        return subprocess.run(
            [sys.executable, "manage.py", "check"],
            cwd=APP_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_debug_accepts_valid_boolean_strings(self):
        result = self._run_manage_check("false")
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}",
        )

    def test_debug_rejects_invalid_boolean_strings_with_clear_error(self):
        result = self._run_manage_check("release")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid boolean value for DEBUG", result.stderr)

    def test_production_boot_requires_turnstile_secret(self):
        env = os.environ.copy()
        env.update(
            {
                "TESTING": "0",
                "DEBUG": "false",
                "DB_NAME": "photobox",
                "DB_PASS": "change-me-strong-db-password",
                "SECRET_KEY": "change-me-to-a-50-char-random-string-in-production",
                "DJANGO_SECRET_KEY": "change-me-to-a-50-char-random-string-in-production",
                "ALLOWED_HOSTS": "api.example.com",
                "CORS_ALLOWED_ORIGINS": "https://app.example.com",
                "CSRF_TRUSTED_ORIGINS": "https://app.example.com",
                "TURNSTILE_SECRET_KEY": "",
            }
        )
        env.pop("PYTEST_CURRENT_TEST", None)
        env["TURNSTILE_SECRET_KEY"] = ""
        env["CLOUDFLARE_TURNSTILE_SECRET_KEY"] = ""
        result = subprocess.run(
            [sys.executable, "manage.py", "check"],
            cwd=APP_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        combined = result.stderr + result.stdout
        self.assertIn("TURNSTILE_SECRET_KEY", combined)
