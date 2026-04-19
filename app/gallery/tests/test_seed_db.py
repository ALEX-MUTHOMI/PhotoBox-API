from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import Workspace
from gallery.models import Event, Scene, Photo


@override_settings(DEBUG=True)
class SeedDbCommandTests(TestCase):
    def test_seed_db_supports_batched_custom_scale(self):
        call_command(
            "seed_db",
            flush=True,
            workspace_count=1,
            events_per_workspace=2,
            scenes_per_event=3,
            photos_per_scene=4,
            batch_size=2,
        )

        self.assertEqual(Workspace.objects.count(), 1)
        self.assertEqual(Event.objects.count(), 2)
        self.assertEqual(Scene.objects.count(), 6)
        self.assertEqual(Photo.objects.count(), 24)

        workspace = Workspace.objects.first()
        expected_bytes = sum(Photo.objects.values_list("file_size_bytes", flat=True))
        self.assertEqual(workspace.storage_used_bytes, expected_bytes)
