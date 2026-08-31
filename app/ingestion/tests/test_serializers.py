from django.test import TestCase
from django.contrib.auth import get_user_model
from unittest.mock import Mock

from ingestion.serializers import BulkManifestSerializer, ManifestFileItemSerializer
from core.models import Workspace
from gallery.models import Event, Scene

User = get_user_model()

class ManifestFileItemSerializerTests(TestCase):
    """
    THE DATA BOUNDARY: Testing individual file payload validation,
    balancing strict security sanitization with legitimate business logic.
    """

    # --- THE ENGINEER'S LOGIC (HAPPY PATHS & STRUCTURE) ---

    def test_valid_image_payload_success(self):
        """THE LOGIC: Proves a standard studio image passes perfectly."""
        payload = {"filename": "headshot.jpg", "file_size": 5000000, "client_reference_id": "uuid-1"}
        serializer = ManifestFileItemSerializer(data=payload)

        self.assertTrue(serializer.is_valid(), f"Failed on valid image: {serializer.errors}")
        self.assertEqual(serializer.validated_data['media_type'], 'IMAGE')
        self.assertEqual(serializer.validated_data['sanitized_filename'], 'headshot.jpg')

    def test_valid_video_payload_success(self):
        """THE LOGIC: Proves a massive 4K drone video is correctly identified and allowed."""
        payload = {"filename": "drone_pan.mp4", "file_size": 4 * 1024 * 1024 * 1024, "client_reference_id": "uuid-2"}
        serializer = ManifestFileItemSerializer(data=payload)

        self.assertTrue(serializer.is_valid(), f"Failed on valid video: {serializer.errors}")
        self.assertEqual(serializer.validated_data['media_type'], 'VIDEO')

    def test_missing_required_fields(self):
        """THE LOGIC: Proves structural integrity. Missing keys must be rejected."""
        payload = {"filename": "img.jpg"}
        serializer = ManifestFileItemSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn("file_size", serializer.errors)
        self.assertIn("client_reference_id", serializer.errors)

    def test_filename_slugification(self):
        """
        THE LOGIC: R2 keys hate spaces and emojis.
        Proves the serializer strips dangerous/annoying characters.
        """
        payload = {
            "filename": "Wedding Photo!  ❤️ Copy (2).jpg",
            "file_size": 50000,
            "client_reference_id": "uuid-1"
        }
        serializer = ManifestFileItemSerializer(data=payload)
        self.assertTrue(serializer.is_valid())

        sanitized = serializer.validated_data['sanitized_filename']
        # It should strip the emojis, spaces, and parentheses, keeping it R2-safe
        self.assertEqual(sanitized, "Wedding_Photo_Copy_2.jpg")

    def test_unicode_filename_folds_to_ascii(self):
        """Accented or CJK characters must not reject the manifest item."""
        payload = {
            "filename": "café-résumé.jpg",
            "file_size": 50000,
            "client_reference_id": "uuid-unicode",
        }
        serializer = ManifestFileItemSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['sanitized_filename'], "cafe-resume.jpg")
        self.assertEqual(serializer.validated_data['media_type'], 'IMAGE')


    # --- THE HACKER'S EXPLOITS (SECURITY) ---

    def test_string_exhaustion_dos(self):
        """THE HACK: Sending a 10MB string as a filename to bloat RAM."""
        massive_string = "A" * 1000000
        payload = {
            "filename": f"{massive_string}.jpg",
            "file_size": 50000,
            "client_reference_id": "uuid-1"
        }
        serializer = ManifestFileItemSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn("filename", serializer.errors)
        self.assertIn("Ensure this field has no more than 255 characters", str(serializer.errors['filename']))

    def test_negative_byte_math_exploit(self):
        payload = {"filename": "hack.jpg", "file_size": -50000, "client_reference_id": "uuid-123"}
        serializer = ManifestFileItemSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn("file_size", serializer.errors)

    def test_type_spoofing_exploit(self):
        payload = {"filename": "img.jpg", "file_size": {"hack": "string"}, "client_reference_id": "uuid-123"}
        serializer = ManifestFileItemSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn("file_size", serializer.errors)

    def test_xss_and_path_traversal_sanitization(self):
        payload = {
            "filename": "../../../<script>alert('xss')</script>wedding.jpg",
            "file_size": 10000,
            "client_reference_id": "uuid-123"
        }
        serializer = ManifestFileItemSerializer(data=payload)
        self.assertTrue(serializer.is_valid())
        sanitized_name = serializer.validated_data['sanitized_filename']
        self.assertNotIn("<script>", sanitized_name)
        self.assertNotIn("../", sanitized_name)

    def test_null_byte_injection_shield(self):
        payload = {"filename": "virus.exe\x00.jpg", "file_size": 10000, "client_reference_id": "uuid-123"}
        serializer = ManifestFileItemSerializer(data=payload)
        self.assertFalse(serializer.is_valid(), "FATAL: Null byte accepted!")

    def test_asymmetric_mime_bomb_image(self):
        payload = {"filename": "pixel_bomb.jpg", "file_size": 5 * 1024 * 1024 * 1024, "client_reference_id": "1"}
        serializer = ManifestFileItemSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn("Exceeds max image size", str(serializer.errors))
        
    def test_executable_spoof_fallback(self):
        payload = {"filename": "virus.exe", "file_size": 10000, "client_reference_id": "1"}
        serializer = ManifestFileItemSerializer(data=payload)
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data['media_type'], 'IMAGE')
        self.assertTrue(serializer.validated_data['sanitized_filename'].endswith('.jpg'))


class BulkManifestSerializerTests(TestCase):
    """
    THE BATCH ORCHESTRATOR: Testing array limits and DB locks.
    """
    def setUp(self):
        self.user = User.objects.create_user(email="dev@photobox.com", password="password123")
        self.workspace = Workspace.objects.create(user=self.user, business_name="PhotoBox Studios")
        self.event = Event.objects.create(workspace=self.workspace, title="Tech Conference", slug="tech-conf")
        self.scene = Scene.objects.create(event=self.event, title="Keynote")

        self.mock_request = Mock()
        self.mock_request.user = self.user

    def test_valid_bulk_manifest_success(self):
        files = [{"filename": f"img_{i}.jpg", "file_size": 1000, "client_reference_id": str(i)} for i in range(5)]
        payload = {"scene_id": str(self.scene.id), "files": files}
        serializer = BulkManifestSerializer(data=payload, context={'request': self.mock_request})
        self.assertTrue(serializer.is_valid(), f"Failed on valid bulk payload: {serializer.errors}")

    def test_bulk_manifest_accepts_unicode_filename_among_ascii(self):
        """One non-ASCII filename must not 400 the entire batch."""
        files = [
            {"filename": "img_0.jpg", "file_size": 1000, "client_reference_id": "0"},
            {"filename": "婚礼照.jpg", "file_size": 1000, "client_reference_id": "1"},
        ]
        payload = {"scene_id": str(self.scene.id), "files": files}
        serializer = BulkManifestSerializer(data=payload, context={'request': self.mock_request})
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_tenant_isolation_cuckoo_attack(self):
        victim = User.objects.create_user(email="victim@photobox.com", password="password123")
        victim_workspace = Workspace.objects.create(user=victim, business_name="Victim Studios")
        victim_event = Event.objects.create(workspace=victim_workspace, title="Private Event", slug="private")
        victim_scene = Scene.objects.create(event=victim_event, title="Dropzone")

        payload = {
            "scene_id": str(victim_scene.id),
            "files": [{"filename": "malware.jpg", "file_size": 1000, "client_reference_id": "1"}]
        }
        serializer = BulkManifestSerializer(data=payload, context={'request': self.mock_request})
        self.assertFalse(serializer.is_valid(), "FATAL: Tenant isolation bypassed!")
        self.assertIn("scene_id", serializer.errors)

    def test_dos_array_overflow(self):
        malicious_files = [{"filename": f"img{i}.jpg", "file_size": 1000, "client_reference_id": str(i)} for i in range(2500)]
        payload = {"scene_id": str(self.scene.id), "files": malicious_files}
        serializer = BulkManifestSerializer(data=payload, context={'request': self.mock_request})
        self.assertFalse(serializer.is_valid())
        self.assertIn("files", serializer.errors)

    def test_empty_array_cpu_waste(self):
        payload = {"scene_id": str(self.scene.id), "files": []}
        serializer = BulkManifestSerializer(data=payload, context={'request': self.mock_request})
        self.assertFalse(serializer.is_valid())
        self.assertIn("files", serializer.errors)




