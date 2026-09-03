"""Phase 4: pHash / LSH / Union-Find burst clustering."""
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from PIL import Image, ImageDraw

from core.models import Workspace
from gallery.burst_cluster import PhashRow, cluster_phash_rows
from gallery.models import Event, Photo, PhotoBurstCluster, Scene
from gallery.phash import (
    collect_lsh_candidate_pairs,
    compute_phash_bytes,
    hamming_distance,
    lsh_band_keys,
)
from gallery.tasks import (
    _schedule_scene_burst_cluster,
    cluster_scene_bursts,
    compute_photo_phash,
)


User = get_user_model()


class PhashLshUnitTests(SimpleTestCase):
    def test_hamming_identical_is_zero(self):
        a = (0).to_bytes(8, "big")
        self.assertEqual(hamming_distance(a, a), 0)

    def test_hamming_known_bit_flips(self):
        a = (0b0).to_bytes(8, "big")
        b = (0b111).to_bytes(8, "big")
        self.assertEqual(hamming_distance(a, b), 3)

    def test_lsh_near_duplicates_share_a_band(self):
        base = (0x0123456789ABCDEF).to_bytes(8, "big")
        # Flip bits in the last 8-bit band only — other 7 bands still match under 8×8.
        near = (0x0123456789ABCD00).to_bytes(8, "big")
        keys_a = set(lsh_band_keys(base, bands=8, rows=8))
        keys_b = set(lsh_band_keys(near, bands=8, rows=8))
        self.assertTrue(keys_a & keys_b)
        self.assertEqual(len(keys_a), 8)

    def test_lsh_defaults_are_8x8_not_16x4(self):
        from django.conf import settings

        self.assertEqual(settings.PHOTO_PHASH_LSH_BANDS, 8)
        self.assertEqual(settings.PHOTO_PHASH_LSH_ROWS, 8)
        self.assertEqual(
            settings.PHOTO_PHASH_LSH_BANDS * settings.PHOTO_PHASH_LSH_ROWS,
            64,
        )

    def test_uf_transitive_cluster(self):
        now = timezone.now()
        a = uuid4()
        b = uuid4()
        c = uuid4()
        # Construct hashes that are close: share bands and low Hamming
        h1 = (0xAAAAAAAAAAAAAAAA).to_bytes(8, "big")
        h2 = (0xAAAAAAABAAAAAAAA).to_bytes(8, "big")  # 1 bit flip
        h3 = (0xAAAAAAABAABAAAAA).to_bytes(8, "big")  # few flips from h2
        rows = [
            PhashRow(a, h1, now),
            PhashRow(b, h2, now + timedelta(seconds=1)),
            PhashRow(c, h3, now + timedelta(seconds=2)),
        ]
        components = cluster_phash_rows(
            rows, hamming_threshold=8, time_window_seconds=90
        )
        self.assertEqual(len(components), 1)
        self.assertEqual(set(components[0]), {a, b, c})

    def test_time_window_blocks_union(self):
        now = timezone.now()
        a = uuid4()
        b = uuid4()
        h = (0xAAAAAAAAAAAAAAAA).to_bytes(8, "big")
        rows = [
            PhashRow(a, h, now),
            PhashRow(b, h, now + timedelta(minutes=10)),
        ]
        components = cluster_phash_rows(
            rows, hamming_threshold=8, time_window_seconds=90
        )
        self.assertEqual(components, [])

    def test_compute_phash_deterministic(self):
        img = Image.new("RGB", (64, 64), color=(12, 34, 56))
        draw = ImageDraw.Draw(img)
        draw.rectangle((10, 10, 40, 40), fill=(200, 100, 50))
        a = compute_phash_bytes(img)
        b = compute_phash_bytes(img.copy())
        self.assertEqual(a, b)
        self.assertEqual(len(a), 8)


class BurstClusterPersistenceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email="burst@test.com",
            password="password123",
            name="Burst",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user, business_name="Burst Studio"
        )
        self.event = Event.objects.create(
            workspace=self.workspace, title="Burst Event", slug="burst-event"
        )
        self.scene = Scene.objects.create(event=self.event, title="Ceremony")
        self.other_scene = Scene.objects.create(event=self.event, title="Reception")
        self.now = timezone.now()

    def _ready_photo(self, scene, name, phash, uploaded_at=None):
        photo = Photo.objects.create(
            scene=scene,
            original_filename=name,
            file_size_bytes=1024,
            status="READY",
            media_type="IMAGE",
            r2_object_key=f"raw/{name}",
            phash=phash,
            phash_version=1,
        )
        Photo.objects.filter(pk=photo.pk).update(
            uploaded_at=uploaded_at or self.now
        )
        photo.refresh_from_db()
        return photo

    def test_cluster_scene_groups_near_duplicates(self):
        h = (0xAAAAAAAAAAAAAAAA).to_bytes(8, "big")
        h2 = (0xAAAAAAABAAAAAAAA).to_bytes(8, "big")
        p1 = self._ready_photo(self.scene, "a.jpg", h, self.now)
        p2 = self._ready_photo(
            self.scene, "b.jpg", h2, self.now + timedelta(seconds=1)
        )
        p3 = self._ready_photo(
            self.scene,
            "c.jpg",
            (0xFFFFFFFFFFFFFFFF).to_bytes(8, "big"),
            self.now + timedelta(seconds=2),
        )

        result = cluster_scene_bursts(str(self.scene.id))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["cluster_count"], 1)

        p1.refresh_from_db()
        p2.refresh_from_db()
        p3.refresh_from_db()
        self.assertIsNotNone(p1.burst_cluster_id)
        self.assertEqual(p1.burst_cluster_id, p2.burst_cluster_id)
        self.assertIsNone(p3.burst_cluster_id)
        self.assertEqual(PhotoBurstCluster.objects.filter(scene=self.scene).count(), 1)

    def test_cross_scene_same_phash_not_merged(self):
        h = (0xAAAAAAAAAAAAAAAA).to_bytes(8, "big")
        a = self._ready_photo(self.scene, "a.jpg", h)
        b = self._ready_photo(self.other_scene, "b.jpg", h)
        cluster_scene_bursts(str(self.scene.id))
        cluster_scene_bursts(str(self.other_scene.id))
        a.refresh_from_db()
        b.refresh_from_db()
        # Singletons stay unclustered
        self.assertIsNone(a.burst_cluster_id)
        self.assertIsNone(b.burst_cluster_id)

    def test_pending_photo_not_hashed(self):
        photo = Photo.objects.create(
            scene=self.scene,
            original_filename="pending.jpg",
            file_size_bytes=1024,
            status="PENDING",
            media_type="IMAGE",
            r2_object_key="raw/pending.jpg",
        )
        result = compute_photo_phash(str(photo.id))
        self.assertEqual(result["status"], "skipped_not_ready")
        photo.refresh_from_db()
        self.assertIsNone(photo.phash)

    @patch("gallery.tasks.cluster_scene_bursts.apply_async")
    def test_debounce_schedules_once(self, mock_apply):
        cache.clear()
        with override_settings(PHOTO_CLUSTER_DEBOUNCE_SECONDS=30):
            _schedule_scene_burst_cluster(str(self.scene.id))
            _schedule_scene_burst_cluster(str(self.scene.id))
        self.assertEqual(mock_apply.call_count, 1)

    def test_fast_lane_ack_does_not_call_cluster_synchronously(self):
        """Upload ACK path schedules process_fast_lane_asset only — not cluster."""
        import inspect

        from gallery import views as gallery_views

        source = inspect.getsource(gallery_views.PhotoFastLaneViewSet.perform_create)
        self.assertIn("process_fast_lane_asset.delay", source)
        self.assertNotIn("cluster_scene_bursts", source)
        self.assertNotIn("compute_photo_phash", source)


class LshPairCollectionTests(SimpleTestCase):
    def test_collect_pairs_from_shared_band(self):
        h = (0x0123456789ABCDEF).to_bytes(8, "big")
        a, b = uuid4(), uuid4()
        pairs = collect_lsh_candidate_pairs([(a, h), (b, h)])
        self.assertEqual(len(pairs), 1)
