"""Unit tests for eager-safe Celery retry helpers."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from celery.exceptions import MaxRetriesExceededError, Retry
from django.test import SimpleTestCase

from core.celery_retry import is_eager_execution, retry_or_call, retry_or_return


class CeleryRetryHelperTests(SimpleTestCase):
    def test_is_eager_when_request_flags_set(self):
        task = SimpleNamespace(request=SimpleNamespace(is_eager=True, called_directly=False))
        self.assertTrue(is_eager_execution(task))
        task = SimpleNamespace(request=SimpleNamespace(is_eager=False, called_directly=True))
        self.assertTrue(is_eager_execution(task))
        task = SimpleNamespace(request=SimpleNamespace(is_eager=False, called_directly=False))
        self.assertFalse(is_eager_execution(task))

    def test_retry_or_return_skips_retry_when_eager(self):
        task = MagicMock()
        task.name = "demo.task"
        task.request = SimpleNamespace(is_eager=True, called_directly=False)
        result = retry_or_return(task, RuntimeError("boom"), fallback={"status": "error"})
        self.assertEqual(result, {"status": "error"})
        task.retry.assert_not_called()

    def test_retry_or_return_raises_retry_in_worker(self):
        task = MagicMock()
        task.name = "demo.task"
        task.request = SimpleNamespace(is_eager=False, called_directly=False)
        task.retry.side_effect = Retry("retry", when=None)
        with self.assertRaises(Retry):
            retry_or_return(task, RuntimeError("boom"), fallback={"status": "error"})

    def test_retry_or_call_runs_exhausted_callback(self):
        task = MagicMock()
        task.name = "demo.task"
        task.request = SimpleNamespace(is_eager=False, called_directly=False)
        task.retry.side_effect = MaxRetriesExceededError("done")
        result = retry_or_call(task, RuntimeError("boom"), on_exhausted=lambda: "exhausted")
        self.assertEqual(result, "exhausted")
