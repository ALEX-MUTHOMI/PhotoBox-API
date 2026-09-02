"""Custom Django test runner with in-memory storage reset and DB teardown hardening."""

import logging

from django.db import connection
from django.test.runner import DiscoverRunner

logger = logging.getLogger(__name__)


class EnterpriseTestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        try:
            from core.utils.inmemory_storage import InMemoryTestStorage
            InMemoryTestStorage.clear()
        except Exception as exc:
            logger.warning("Could not reset in-memory test storage before test run: %s", exc)

    def teardown_databases(self, old_config, **kwargs):
        """
        Forcefully disconnect all sessions from the specific test DB before dropping it.
        This mitigates 'ObjectInUse' race conditions in asynchronous or heavily concurrent architectures.
        """
        test_db_name = connection.settings_dict.get('NAME', '<unknown>')
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT pg_terminate_backend(pg_stat_activity.pid)
                    FROM pg_stat_activity
                    WHERE pg_stat_activity.datname = %s
                      AND pid <> pg_backend_pid();
                    """,
                    [test_db_name]
                )
        except Exception as e:
            logger.warning(f"Could not force-terminate DB connections for {test_db_name}: {e}")

        super().teardown_databases(old_config, **kwargs)

    def teardown_test_environment(self, **kwargs):
        try:
            from core.utils.inmemory_storage import InMemoryTestStorage
            InMemoryTestStorage.clear()
        except Exception as exc:
            logger.warning("Could not reset in-memory test storage after test run: %s", exc)
        super().teardown_test_environment(**kwargs)
