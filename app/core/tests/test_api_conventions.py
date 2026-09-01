from uuid import uuid4

from django.apps import apps
from django.test import TestCase
from django.urls import resolve, reverse


class ApiConventionTests(TestCase):
    def test_photographer_and_client_prefixes_remain_distinct(self):
        photographer = resolve(reverse('gallery:event-list'))
        client_gallery = resolve(
            reverse('gallery_public:detail', kwargs={'gallery_id': uuid4()})
        )
        self.assertNotEqual(
            photographer.func.cls.__name__,
            client_gallery.func.cls.__name__,
        )

    def test_core_app_has_no_legacy_gallery_models(self):
        core_models = {model.__name__ for model in apps.get_app_config('core').get_models()}
        self.assertNotIn('Gallery', core_models)
        self.assertNotIn('Image', core_models)

    def test_dual_key_error_helper(self):
        from core.api_errors import dual_key_error
        from rest_framework import status as drf_status

        response = dual_key_error('blocked', status_code=drf_status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data['error'], 'blocked')
        self.assertEqual(response.data['detail'], 'blocked')
