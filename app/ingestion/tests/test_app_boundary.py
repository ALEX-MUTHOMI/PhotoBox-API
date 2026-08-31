from django.apps import apps
from django.test import TestCase

from gallery.models import Photo
from ingestion.models import MediaAsset


class IngestionAppBoundaryTests(TestCase):
    def test_ingestion_declares_no_local_models(self):
        ingestion_models = {
            model.__name__
            for model in apps.get_app_config("ingestion").get_models()
        }
        self.assertEqual(ingestion_models, set())

    def test_media_asset_alias_points_at_photo(self):
        self.assertIs(MediaAsset, Photo)
