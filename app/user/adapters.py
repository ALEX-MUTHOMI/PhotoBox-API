"""
Allauth adapter hardening for social-auth account linking.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.http import JsonResponse

from allauth.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialAccount


User = get_user_model()


class HardenedSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Reject ambiguous or unverified social identities before allauth can attach
    them to a local user.
    """

    def pre_social_login(self, request, sociallogin):
        super().pre_social_login(request, sociallogin)

        email = (getattr(sociallogin.user, "email", "") or "").strip().lower()
        if not email:
            raise ImmediateHttpResponse(
                JsonResponse(
                    {"detail": "Social account did not provide an email address."},
                    status=400,
                )
            )

        verified = False
        for email_address in getattr(sociallogin, "email_addresses", []) or []:
            if (email_address.email or "").strip().lower() == email and email_address.verified:
                verified = True
                break

        if not verified:
            verified = bool(getattr(sociallogin.account, "extra_data", {}).get("email_verified"))

        if not verified:
            raise ImmediateHttpResponse(
                JsonResponse(
                    {"detail": "Social account email must be verified."},
                    status=400,
                )
            )

        existing_user = User.objects.filter(email__iexact=email).first()
        if not existing_user:
            return

        linked_account = SocialAccount.objects.filter(
            user=existing_user,
            provider=sociallogin.account.provider,
            uid=sociallogin.account.uid,
        ).exists()

        if linked_account:
            return

        raise ImmediateHttpResponse(
            JsonResponse(
                {
                    "detail": (
                        "An account with this email already exists. "
                        "Sign in with your existing method and link Google from settings."
                    )
                },
                status=409,
            )
        )
