"""
Tests for the health check API.
"""
from django.test import TestCase
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APIClient


class HealthCheckTests(TestCase):
    """Test the health check API."""

    def test_readiness_health_check(self):
        """Deep readiness probe returns JSON with dependency checks."""
        client = APIClient()
        url = reverse('health-check')
        res = client.get(url)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.json()["healthy"])

    def test_liveness_probe(self):
        client = APIClient()
        res = client.get(reverse("health"))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.content.decode("utf-8"), "ok")
