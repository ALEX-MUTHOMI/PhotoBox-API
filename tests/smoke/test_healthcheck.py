import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_healthcheck_route(client):
    response = client.get(reverse("health-check"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["healthy"] is True
    assert payload["checks"]["database"] == "ok"
    assert payload["checks"]["cache"] == "ok"


@pytest.mark.django_db
def test_deploy_health_alias_is_liveness(client):
    response = client.get(reverse("health"))

    assert response.status_code == 200
    assert response.content.decode("utf-8") == "ok"
    assert "text/plain" in response["Content-Type"]
