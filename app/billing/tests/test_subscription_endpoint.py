from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from billing.models import Subscription
from core.models import Workspace

User = get_user_model()
SUBSCRIPTION_URL = reverse('billing:subscription-status')


class SubscriptionStatusEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email='pro@example.com', password='pass')
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name='Pro Studio',
            storage_used_bytes=2 * 1024 ** 3,
            storage_limit_bytes=8 * 1024 ** 3,
        )
        self.subscription = Subscription.objects.get(user=self.user)
        self.subscription.is_pro = True
        self.subscription.storage_used_bytes = 0
        self.subscription.storage_limit_bytes = 1
        self.subscription.save(update_fields=['is_pro', 'storage_used_bytes', 'storage_limit_bytes'])

    def test_unauthenticated_returns_401(self):
        res = self.client.get(SUBSCRIPTION_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_photographer_reads_workspace_usage(self):
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.user)}'
        )
        res = self.client.get(SUBSCRIPTION_URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['storage_used_bytes'], self.workspace.storage_used_bytes)
        self.assertEqual(res.data['storage_limit_bytes'], self.workspace.storage_limit_bytes)
        self.assertTrue(res.data['is_pro'])
        self.assertNotIn('lemon_squeezy_customer_id', res.data)
        self.assertNotIn('lemon_squeezy_subscription_id', res.data)

    def test_client_jwt_cannot_read_subscription(self):
        self.user.gallery_id = '00000000-0000-0000-0000-000000000001'
        self.client.force_authenticate(user=self.user)
        res = self.client.get(SUBSCRIPTION_URL)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_patch_not_allowed(self):
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.user)}'
        )
        res = self.client.patch(SUBSCRIPTION_URL, {'is_pro': True}, format='json')
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
