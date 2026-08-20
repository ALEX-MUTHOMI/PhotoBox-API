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
