import pytest
from django.core.cache import cache
from django.db import connection
from django.urls import reverse


@pytest.mark.django_db
def test_django_postgres_and_health(client):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        assert cursor.fetchone()[0] == 1

    response = client.get(reverse("health-check"))

    assert response.status_code == 200
    assert response.json() == {"healthy": True}


def test_cache_round_trip():
    cache.set("photobox-ci-smoke", "ok", timeout=10)

    assert cache.get("photobox-ci-smoke") == "ok"
