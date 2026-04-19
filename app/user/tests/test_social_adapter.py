from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from allauth.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount

from user.adapters import HardenedSocialAccountAdapter


User = get_user_model()


class HardenedSocialAccountAdapterTests(TestCase):
    def setUp(self):
        self.adapter = HardenedSocialAccountAdapter()
        self.request = RequestFactory().get("/api/user/google/")

    def _sociallogin(self, email, verified=True, uid="google-uid-1"):
        email_address = SimpleNamespace(email=email, verified=verified)
        return SimpleNamespace(
            user=SimpleNamespace(email=email),
            account=SimpleNamespace(
                provider="google",
                uid=uid,
                extra_data={"email_verified": verified},
            ),
            email_addresses=[email_address],
        )

    def test_unverified_google_email_is_rejected(self):
        sociallogin = self._sociallogin("victim@example.com", verified=False)

        with self.assertRaises(ImmediateHttpResponse) as ctx:
            self.adapter.pre_social_login(self.request, sociallogin)

        self.assertEqual(ctx.exception.response.status_code, 400)

    def test_existing_password_account_cannot_be_auto_linked(self):
        User.objects.create_user(
            email="victim@example.com",
            password="StrongPassword123!",
            accepted_terms=True,
        )
        sociallogin = self._sociallogin("victim@example.com", verified=True, uid="attacker-google-uid")

        with self.assertRaises(ImmediateHttpResponse) as ctx:
            self.adapter.pre_social_login(self.request, sociallogin)

        self.assertEqual(ctx.exception.response.status_code, 409)

    def test_existing_linked_google_account_is_allowed(self):
        user = User.objects.create_user(
            email="linked@example.com",
            password="StrongPassword123!",
            accepted_terms=True,
        )
        SocialAccount.objects.create(
            user=user,
            provider="google",
            uid="known-google-uid",
            extra_data={"email": "linked@example.com", "email_verified": True},
        )
        sociallogin = self._sociallogin("linked@example.com", verified=True, uid="known-google-uid")

        self.adapter.pre_social_login(self.request, sociallogin)
