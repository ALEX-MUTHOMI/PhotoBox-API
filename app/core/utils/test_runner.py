import logging
from django.test.runner import DiscoverRunner
from django.db import connection

logger = logging.getLogger(__name__)


class EnterpriseTestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        try:
            from core.utils.test_storage import InMemoryTestStorage
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
            # Dynamically fetch the actual name of the database currently in use
            with connection.cursor() as cursor:
                # Parameterized query to prevent SQLi, executing a ruthless kill command
                # against any PID (Process ID) that isn't the current test runner's PID.
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
            # Log the warning rather than printing, allowing CI log aggregators to parse it cleanly.
            logger.warning(f"Could not force-terminate DB connections for {test_db_name}: {e}")

        # Proceed with the native Django teardown sequence
        super().teardown_databases(old_config, **kwargs)

    def teardown_test_environment(self, **kwargs):
        try:
            from core.utils.test_storage import InMemoryTestStorage
            InMemoryTestStorage.clear()
        except Exception as exc:
            logger.warning("Could not reset in-memory test storage after test run: %s", exc)
        super().teardown_test_environment(**kwargs)
