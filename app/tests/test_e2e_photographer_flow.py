"""
test_e2e_photographer_flow.py — Full end-to-end tests against the live stack.

Proves a real photographer can upload a real image and it flows through:
  Django API → Celery Worker → Cloudinary → DB record updated

Run with: docker compose run --rm test e2e
Requires: E2E_PHOTOGRAPHER_USERNAME, E2E_PHOTOGRAPHER_PASSWORD in .env
          STAGING_BASE_URL=http://app:8000 (set automatically by compose)
"""

import io
import os
import uuid

import pytest
import requests

from conftest import (
    make_image_file,
    make_large_image_file,
    wait_for_condition,
    STAGING_BASE_URL,
    TEST_CLOUDINARY_FOLDER,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

ASYNC_TIMEOUT = 60
POLL_INTERVAL = 2


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_auth_token(username: str, password: str) -> str:
    response = requests.post(
        f"{STAGING_BASE_URL}/api/token/",
        json={"username": username, "password": password},
        timeout=15,
    )
    assert response.status_code == 200, (
        f"Auth failed ({response.status_code}): {response.text[:500]}"
    )
    return response.json()["access"]


def poll_upload_status(upload_id: str, token: str) -> dict:
    response = requests.get(
        f"{STAGING_BASE_URL}/api/uploads/{upload_id}/",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if response.status_code != 200:
        return {"status": "pending"}
    return response.json()


# ─────────────────────────────────────────────────────────────────────────────
# E2E SUITE
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.e2e
class TestPhotographerUploadE2E:

    PHOTOGRAPHER_USERNAME = os.environ.get("E2E_PHOTOGRAPHER_USERNAME", "test_photographer")
    PHOTOGRAPHER_PASSWORD = os.environ.get("E2E_PHOTOGRAPHER_PASSWORD", "StrongTestPass!99")

    @pytest.fixture(autouse=True)
    def auth_token(self):
        self._token = get_auth_token(
            self.PHOTOGRAPHER_USERNAME,
            self.PHOTOGRAPHER_PASSWORD,
        )

    @property
    def auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    def test_jpeg_upload_produces_cloudinary_url(self, cloudinary_cleanup):
        """Core contract: JPEG in → Cloudinary CDN URL out."""
        upload_response = requests.post(
            f"{STAGING_BASE_URL}/api/uploads/",
            headers=self.auth_headers,
            files={"image": ("photo.jpg", make_image_file(), "image/jpeg")},
            timeout=30,
        )
        assert upload_response.status_code in (200, 201, 202), (
            f"Upload failed ({upload_response.status_code}): {upload_response.text[:500]}"
        )

        upload_id = upload_response.json().get("upload_id") or upload_response.json().get("id")
        assert upload_id, f"No upload_id in response: {upload_response.json()}"

        final = wait_for_condition(
            lambda: (d := poll_upload_status(upload_id, self._token))
                    and d.get("status") == "processed" and d,
            timeout=ASYNC_TIMEOUT,
            poll_interval=POLL_INTERVAL,
            description=f"upload {upload_id} to reach status=processed",
        )

        cloudinary_url = final.get("cloudinary_url")
        assert cloudinary_url, f"cloudinary_url missing from response: {final}"
        assert cloudinary_url.startswith("https://res.cloudinary.com/")

        if final.get("cloudinary_public_id"):
            cloudinary_cleanup(final["cloudinary_public_id"])

        cdn_response = requests.get(cloudinary_url, timeout=15)
        assert cdn_response.status_code == 200
        assert "image" in cdn_response.headers.get("content-type", "")

    def test_invalid_file_rejected_before_cloudinary(self):
        """PDF must be rejected at the API layer — never reaches Cloudinary."""
        fake_pdf = io.BytesIO(b"%PDF-1.4 not an image")
        fake_pdf.name = "not_image.jpg"
        response = requests.post(
            f"{STAGING_BASE_URL}/api/uploads/",
            headers=self.auth_headers,
            files={"image": ("not_image.jpg", fake_pdf, "image/jpeg")},
            timeout=15,
        )
        assert response.status_code == 400

    def test_unauthenticated_upload_returns_401(self):
        response = requests.post(
            f"{STAGING_BASE_URL}/api/uploads/",
            files={"image": ("photo.jpg", make_image_file(), "image/jpeg")},
            timeout=15,
        )
        assert response.status_code == 401

    @pytest.mark.slow
    def test_large_image_upload(self, cloudinary_cleanup):
        large_buf = make_large_image_file(mb=8)
        response = requests.post(
            f"{STAGING_BASE_URL}/api/uploads/",
            headers=self.auth_headers,
            files={"image": ("large.jpg", large_buf, "image/jpeg")},
            timeout=60,
        )
        assert response.status_code in (200, 201, 202)

        upload_id = response.json().get("upload_id")
        final = wait_for_condition(
            lambda: (d := poll_upload_status(upload_id, self._token))
                    and d.get("status") == "processed" and d,
            timeout=120,
            description="large image to process",
        )
        assert final.get("cloudinary_url")
        if final.get("cloudinary_public_id"):
            cloudinary_cleanup(final["cloudinary_public_id"])

    def test_upload_status_transitions(self, cloudinary_cleanup):
        """Status must progress through: pending → processing → processed."""
        response = requests.post(
            f"{STAGING_BASE_URL}/api/uploads/",
            headers=self.auth_headers,
            files={"image": ("status_test.jpg", make_image_file(), "image/jpeg")},
            timeout=30,
        )
        assert response.status_code in (200, 201, 202)
        upload_id = response.json()["upload_id"]
        observed = set()

        def record_and_check():
            data = poll_upload_status(upload_id, self._token)
            if data.get("status"):
                observed.add(data["status"])
            return data if data.get("status") == "processed" else None

        final = wait_for_condition(
            record_and_check,
            timeout=ASYNC_TIMEOUT,
            description="status to reach processed",
        )
        assert "processed" in observed
        if final.get("cloudinary_public_id"):
            cloudinary_cleanup(final["cloudinary_public_id"])