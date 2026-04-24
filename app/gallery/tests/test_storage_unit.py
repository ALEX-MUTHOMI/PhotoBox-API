from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.test import SimpleTestCase, override_settings

from gallery.storage import (
    DOWNLOAD_URL_TTL_SECONDS,
    UPLOAD_URL_TTL_SECONDS,
    R2KeyValidationError,
    generate_r2_presigned_get_url,
    generate_r2_presigned_post,
    infer_content_type,
    r2_object_exists,
    validate_r2_key,
)


R2_SETTINGS = dict(
    CLOUDFLARE_R2_ENDPOINT="https://test.r2.cloudflarestorage.com",
    CLOUDFLARE_R2_BUCKET_NAME="test-bucket",
    CLOUDFLARE_ACCESS_KEY_ID="test-key",
    CLOUDFLARE_SECRET_ACCESS_KEY="test-secret",
)


@override_settings(**R2_SETTINGS)
class R2StorageUnitTests(SimpleTestCase):
    def test_validate_r2_key_accepts_safe_key(self):
        key = "fast-lane/tenant_1/photo_1/image.jpg"
        self.assertEqual(validate_r2_key(key), key)

    def test_validate_r2_key_rejects_empty_key(self):
        with self.assertRaises(R2KeyValidationError):
            validate_r2_key("")

    def test_validate_r2_key_rejects_raw_path_traversal(self):
        with self.assertRaises(R2KeyValidationError):
            validate_r2_key("../secret.txt")

    def test_validate_r2_key_rejects_url_encoded_path_traversal(self):
        with self.assertRaises(R2KeyValidationError):
            validate_r2_key("fast-lane/%2e%2e/secret.txt")

    def test_validate_r2_key_rejects_null_byte(self):
        with self.assertRaises(R2KeyValidationError):
            validate_r2_key("fast-lane/image.jpg\x00")

    def test_validate_r2_key_rejects_unsafe_characters(self):
        with self.assertRaises(R2KeyValidationError):
            validate_r2_key("fast-lane/image with spaces$.jpg")

    @patch("gallery.storage.get_r2_client")
    def test_generate_presigned_get_url_clamps_ttl(self, mock_get_r2_client):
        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = "https://signed.example.com"
        mock_get_r2_client.return_value = mock_client

        url = generate_r2_presigned_get_url(
            bucket="test-bucket",
            key="fast-lane/tenant_1/photo_1/image.jpg",
            expires_in=9999,
        )

        self.assertEqual(url, "https://signed.example.com")
        self.assertEqual(
            mock_client.generate_presigned_url.call_args.kwargs["ExpiresIn"],
            DOWNLOAD_URL_TTL_SECONDS,
        )

    def test_generate_presigned_get_url_rejects_bucket_mismatch(self):
        url = generate_r2_presigned_get_url(
            bucket="foreign-bucket",
            key="fast-lane/tenant_1/photo_1/image.jpg",
        )

        self.assertIsNone(url)

    def test_generate_presigned_post_rejects_non_positive_sizes(self):
        self.assertIsNone(
            generate_r2_presigned_post(
                r2_object_key="raw/tenant_1/scene_1/file.jpg",
                max_size_bytes=0,
            )
        )

    @patch("gallery.storage.get_r2_client")
    def test_generate_presigned_post_clamps_ttl(self, mock_get_r2_client):
        mock_client = MagicMock()
        mock_client.generate_presigned_post.return_value = {
            "url": "https://upload.example.com",
            "fields": {"key": "value"},
        }
        mock_get_r2_client.return_value = mock_client

        result = generate_r2_presigned_post(
            r2_object_key="raw/tenant_1/scene_1/file.jpg",
            max_size_bytes=4096,
            expires_in=9999,
        )

        self.assertEqual(result["upload_url"], "https://upload.example.com")
        self.assertEqual(result["post_url"], "https://upload.example.com")
        self.assertEqual(result["post_fields"], {"key": "value"})
        self.assertEqual(
            mock_client.generate_presigned_post.call_args.kwargs["ExpiresIn"],
            UPLOAD_URL_TTL_SECONDS,
        )

    @patch("gallery.storage.get_r2_client")
    def test_r2_object_exists_returns_false_for_404(self, mock_get_r2_client):
        mock_client = MagicMock()
        mock_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404"}},
            "HeadObject",
        )
        mock_get_r2_client.return_value = mock_client

        exists = r2_object_exists("raw/tenant_1/scene_1/file.jpg")

        self.assertFalse(exists)

    def test_infer_content_type_defaults_to_octet_stream_for_empty(self):
        self.assertEqual(infer_content_type(""), "application/octet-stream")

    def test_infer_content_type_blocks_null_byte(self):
        self.assertEqual(
            infer_content_type("shell.php\x00.jpg"),
            "application/octet-stream",
        )

    def test_infer_content_type_maps_jpeg(self):
        self.assertEqual(infer_content_type("hero.jpeg"), "image/jpeg")

    def test_infer_content_type_defaults_for_unknown_extension(self):
        self.assertEqual(infer_content_type("archive.bin"), "application/octet-stream")

