"""
test_cloudinary_integration.py — Tests against the REAL Cloudinary API.

These tests prove your credentials work and images flow through correctly.
They upload real files and delete them on teardown via cloudinary_cleanup.

Run with: docker compose run --rm test cloudinary
Requires: CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET in .env
"""

import requests
import pytest
import cloudinary
import cloudinary.uploader
import cloudinary.api

from conftest import make_image_file, make_large_image_file, TEST_CLOUDINARY_FOLDER

pytestmark = [pytest.mark.cloudinary, pytest.mark.integration]


class TestCloudinaryConnectivity:

    def test_credentials_are_valid(self, configure_cloudinary):
        result = cloudinary.api.usage()
        assert "plan" in result, (
            "Cloudinary usage() failed. Check CLOUDINARY_CLOUD_NAME / API_KEY / API_SECRET."
        )


class TestCloudinaryUpload:

    def test_jpeg_upload_returns_secure_url(self, cloudinary_cleanup):
        result = cloudinary.uploader.upload(
            make_image_file(fmt="JPEG"),
            folder=TEST_CLOUDINARY_FOLDER,
            tags=["pytest"],
        )
        cloudinary_cleanup(result["public_id"])
        assert result["secure_url"].startswith("https://")
        assert result["format"] == "jpg"

    def test_uploaded_image_is_reachable_on_cdn(self, cloudinary_cleanup):
        result = cloudinary.uploader.upload(
            make_image_file(width=400, height=300),
            folder=TEST_CLOUDINARY_FOLDER,
            tags=["pytest"],
        )
        cloudinary_cleanup(result["public_id"])

        cdn_response = requests.get(result["secure_url"], timeout=15)
        assert cdn_response.status_code == 200
        assert "image" in cdn_response.headers.get("content-type", "")

    def test_upload_preserves_dimensions(self, cloudinary_cleanup):
        w, h = 1920, 1080
        result = cloudinary.uploader.upload(
            make_image_file(width=w, height=h),
            folder=TEST_CLOUDINARY_FOLDER,
            tags=["pytest"],
        )
        cloudinary_cleanup(result["public_id"])
        assert result["width"] == w
        assert result["height"] == h

    def test_png_upload(self, cloudinary_cleanup):
        result = cloudinary.uploader.upload(
            make_image_file(fmt="PNG", filename="test.png"),
            folder=TEST_CLOUDINARY_FOLDER,
            tags=["pytest"],
        )
        cloudinary_cleanup(result["public_id"])
        assert result["format"] == "png"

    def test_large_image_upload(self, cloudinary_cleanup):
        result = cloudinary.uploader.upload_large(
            make_large_image_file(mb=8),
            folder=TEST_CLOUDINARY_FOLDER,
            chunk_size=6_000_000,
            tags=["pytest", "large"],
        )
        cloudinary_cleanup(result["public_id"])
        assert result["bytes"] > 0
        assert result["secure_url"].startswith("https://")


class TestCloudinaryServiceWrapper:
    """Test your gallery.services.cloudinary_service wrapper, not the raw SDK."""

    def test_service_upload_returns_expected_schema(self, cloudinary_cleanup):
        from gallery.services.cloudinary_service import upload as service_upload

        result = service_upload(make_image_file(), folder=TEST_CLOUDINARY_FOLDER)
        cloudinary_cleanup(result["public_id"])

        required_keys = {"public_id", "secure_url", "width", "height", "format", "bytes"}
        missing = required_keys - set(result.keys())
        assert not missing, f"Service response missing keys: {missing}"

    def test_service_raises_on_invalid_credentials(self, mocker):
        import cloudinary.exceptions
        mocker.patch(
            "cloudinary.uploader.upload",
            side_effect=cloudinary.exceptions.AuthorizationRequired("Invalid credentials"),
        )
        from gallery.services.cloudinary_service import upload as service_upload

        with pytest.raises(Exception):
            service_upload(make_image_file())
