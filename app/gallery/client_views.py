"""Public gallery HTTP endpoints for clients and guests."""

import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Prefetch, Window
from django.db.models.functions import RowNumber
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, Throttled
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from gallery.client_auth import (
    GalleryCookieJWTAuthentication,
    clear_gallery_access_cookie,
    clear_gallery_access_session_cookie,
    hash_magic_link_token,
    issue_gallery_access_token,
    normalize_gallery_email,
    resolve_gallery_access_session,
    set_gallery_access_cookie,
    set_gallery_access_session_cookie,
)
from gallery.client_ip import get_request_client_ip
from gallery.client_permissions import (
    HasClientGalleryAccess,
    HasClientOrGuestGalleryAccess,
)
from gallery.client_serializers import (
    FavoriteSelectionSerializer,
    FavoriteSelectionWriteSerializer,
    GalleryPublicPhotoSerializer,
    GalleryPublicSerializer,
    GuestAccessSerializer,
    MagicLinkConsumeSerializer,
    MagicLinkRequestSerializer,
)
from gallery.models import (
    ClientAllowlist,
    Event,
    FavoriteSelection,
    GalleryAccessRole,
    GalleryAccessSession,
    GalleryArchiveJob,
    GalleryArchiveType,
    GalleryMagicLink,
    Photo,
    Scene,
    VisibilityChoices,
)
from gallery.pagination import ClientScenePhotoKeysetPagination
from gallery.pin_gate import clear_pin_failures, pin_gate_precheck, record_pin_failure
from gallery.response_headers import GallerySecurityHeadersMixin, apply_gallery_security_headers
from gallery.share_code import is_valid_share_code_format
from gallery.storage import generate_r2_presigned_get_url
from gallery.tasks import (
    _enqueue_archive_job,
    _maybe_resume_stale_archive_job,
    send_gallery_magic_link_email,
)
from gallery.throttles import (
    FavoriteSelectionThrottle,
    GallerySessionReadThrottle,
    GuestAccessThrottle,
    MagicLinkConsumeThrottle,
    MagicLinkSendThrottle,
    ShareCodeProbeThrottle,
)


class PublishedGalleryMixin:
    """Resolve published galleries by share_code only on the public surface."""

    def get_gallery(self):
        code = self.kwargs.get("share_code")
        if not code or not is_valid_share_code_format(code):
            raise NotFound("Gallery not found.")

        gallery = Event.objects.filter(
            share_code=code,
            is_published=True,
            workspace__is_deleted=False,
        ).first()
        if not gallery:
            raise NotFound("Gallery not found.")

        if gallery.expires_at and gallery.expires_at <= timezone.now():
            raise NotFound("Gallery not found.")

        return gallery

    def enforce_gallery_scope(self, request):
        token_gallery_id = str((request.auth or {}).get("gallery_id", ""))
        gallery = self.get_gallery()
        if token_gallery_id != str(gallery.id):
            raise PermissionDenied("Gallery scope mismatch.")
        return gallery

    def get_access_session(self, request, gallery):
        return resolve_gallery_access_session(request, gallery)


class AnonymousUuidGalleryTrapView(APIView):
    """UUID is not a public gallery door — always 404 (hole 8)."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        response = Response({"detail": "Gallery not found."}, status=status.HTTP_404_NOT_FOUND)
        return apply_gallery_security_headers(response)

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)


class GalleryMagicLinkRequestView(GallerySecurityHeadersMixin, PublishedGalleryMixin, APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [MagicLinkSendThrottle]

    def post(self, request, *args, **kwargs):
        gallery = self.get_gallery()
        serializer = MagicLinkRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        if ClientAllowlist.objects.filter(gallery=gallery, email=email).exists():
            now = timezone.now()
            GalleryMagicLink.objects.filter(
                gallery=gallery,
                email=email,
                expires_at__lte=now,
            ).delete()

            live_links = GalleryMagicLink.objects.filter(
                gallery=gallery,
                email=email,
                expires_at__gt=now,
            ).count()

            if live_links < settings.GALLERY_MAGIC_LINK_MAX_LIVE:
                raw_token = secrets.token_urlsafe(64)
                GalleryMagicLink.objects.create(
                    gallery=gallery,
                    email=email,
                    token_hash=hash_magic_link_token(raw_token),
                    expires_at=now + timedelta(minutes=15),
                )
                frontend_url = getattr(settings, "FRONTEND_URL", "https://app.photobox.app").rstrip("/")
                magic_url = f"{frontend_url}/gallery-access#token={raw_token}"
                send_gallery_magic_link_email.delay(
                    email,
                    gallery.title,
                    magic_url,
                )

        return Response(
            {"detail": "If that address is approved, a magic link will be sent."},
            status=status.HTTP_202_ACCEPTED,
        )


class GalleryMagicLinkConsumeView(GallerySecurityHeadersMixin, APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [MagicLinkConsumeThrottle]

    def post(self, request, *args, **kwargs):
        serializer = MagicLinkConsumeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_token = serializer.validated_data["token"]
        token_hash = hash_magic_link_token(raw_token)

        with transaction.atomic():
            magic_link = (
                GalleryMagicLink.objects
                .select_for_update()
                .select_related("gallery__workspace")
                .filter(token_hash=token_hash)
                .first()
            )
            if not magic_link:
                raise PermissionDenied("Invalid or already-used magic link.")

            if magic_link.expires_at <= timezone.now():
                magic_link.delete()
                return Response(
                    {"detail": "Invalid or already-used magic link."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            gallery = magic_link.gallery
            if gallery.expires_at and gallery.expires_at <= timezone.now():
                magic_link.delete()
                return Response(
                    {"detail": "Invalid or already-used magic link."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            if not gallery.is_published or gallery.workspace.is_deleted:
                magic_link.delete()
                return Response(
                    {"detail": "Invalid or already-used magic link."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            session = GalleryAccessSession.objects.create(
                gallery=gallery,
                email=magic_link.email,
                role=GalleryAccessRole.CLIENT,
            )
            token = issue_gallery_access_token(
                gallery_id=gallery.id,
                email=magic_link.email,
                role=GalleryAccessRole.CLIENT,
                pin_version=gallery.pin_version,
            )
            gallery_id = str(gallery.id)
            email = magic_link.email
            magic_link.delete()

        response = Response(
            {
                "authenticated": True,
                "gallery_id": gallery_id,
                "share_code": gallery.share_code,
                "email": email,
                "role": GalleryAccessRole.CLIENT,
                "session_id": session.id,
            },
            status=status.HTTP_200_OK,
        )
        set_gallery_access_cookie(response, token, role=GalleryAccessRole.CLIENT)
        return set_gallery_access_session_cookie(
            response, session.id, role=GalleryAccessRole.CLIENT
        )


class GalleryGuestAccessView(GallerySecurityHeadersMixin, PublishedGalleryMixin, APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [GuestAccessThrottle]

    def post(self, request, *args, **kwargs):
        gallery = self.get_gallery()
        serializer = GuestAccessSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_pin = serializer.validated_data.get("pin") or ""
        email = serializer.validated_data.get("email")

        # Idempotent reuse only when existing GUEST cookie still matches pin_version.
        try:
            from gallery.client_auth import (
                decode_gallery_access_token,
                _cookie_name,
            )

            raw_token = request.COOKIES.get(_cookie_name())
            if raw_token:
                payload = decode_gallery_access_token(raw_token)
                if (
                    payload.get("role") == GalleryAccessRole.GUEST
                    and str(payload.get("gallery_id")) == str(gallery.id)
                    and int(payload.get("pv", 0) or 0) == int(gallery.pin_version or 0)
                ):
                    request.user = type(
                        "P",
                        (),
                        {
                            "is_authenticated": True,
                            "gallery_id": str(gallery.id),
                            "email": payload["email"],
                            "role": GalleryAccessRole.GUEST,
                        },
                    )()
                    request.auth = payload
                    try:
                        session = resolve_gallery_access_session(request, gallery)
                    except PermissionDenied:
                        session = None
                    if session is not None:
                        response = Response(
                            {
                                "authenticated": True,
                                "gallery_id": str(gallery.id),
                                "share_code": gallery.share_code,
                                "email": session.email,
                                "role": GalleryAccessRole.GUEST,
                                "session_id": session.id,
                            },
                            status=status.HTTP_200_OK,
                        )
                        return apply_gallery_security_headers(response)
        except Exception:
            pass

        if not gallery.has_pin:
            raise PermissionDenied("Gallery PIN is required.")

        # Missing PIN: 403, no cookies, no hasher, no lockout increment.
        if not raw_pin:
            raise PermissionDenied("Gallery PIN is required.")

        client_ip = get_request_client_ip(request)
        gate = pin_gate_precheck(gallery.id, client_ip)
        if not gate.allowed:
            raise Throttled(
                wait=gate.retry_after or settings.GALLERY_PIN_LOCKOUT_SECONDS,
                detail="Too many failed PIN attempts for this gallery.",
            )

        if not gallery.check_pin(raw_pin):
            record_pin_failure(gallery.id, client_ip)
            raise PermissionDenied("Invalid gallery PIN.")

        clear_pin_failures(gallery.id, client_ip)

        if not email:
            email = normalize_gallery_email(f"guest:{uuid.uuid4()}@photobox.invalid")

        session = GalleryAccessSession.objects.create(
            gallery=gallery,
            email=email,
            role=GalleryAccessRole.GUEST,
        )
        token = issue_gallery_access_token(
            gallery_id=gallery.id,
            email=email,
            role=GalleryAccessRole.GUEST,
            pin_version=gallery.pin_version,
        )
        response = Response(
            {
                "authenticated": True,
                "gallery_id": str(gallery.id),
                "share_code": gallery.share_code,
                "email": email,
                "role": GalleryAccessRole.GUEST,
                "session_id": session.id,
            },
            status=status.HTTP_200_OK,
        )
        set_gallery_access_cookie(response, token, role=GalleryAccessRole.GUEST)
        return set_gallery_access_session_cookie(
            response, session.id, role=GalleryAccessRole.GUEST
        )


class GalleryGuestLogoutView(GallerySecurityHeadersMixin, PublishedGalleryMixin, APIView):
    authentication_classes = [GalleryCookieJWTAuthentication]
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        # Always clear cookies; optionally resolve gallery for consistent 200.
        try:
            self.get_gallery()
        except NotFound:
            pass
        response = Response({"detail": "Logged out."}, status=status.HTTP_200_OK)
        clear_gallery_access_cookie(response)
        clear_gallery_access_session_cookie(response)
        return response


class PublicGalleryDetailView(GallerySecurityHeadersMixin, PublishedGalleryMixin, APIView):
    authentication_classes = [GalleryCookieJWTAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [GallerySessionReadThrottle, ShareCodeProbeThrottle]

    def get(self, request, *args, **kwargs):
        gallery = self.get_gallery()

        if not (
            getattr(request.user, "is_authenticated", False)
            and getattr(request.user, "role", None) in (
                GalleryAccessRole.CLIENT,
                GalleryAccessRole.GUEST,
            )
        ):
            # No title/cover/photos for crawlers or link unfurls (holes 3).
            return Response(
                {
                    "authenticated": False,
                    "detail": "Authentication required.",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        self.enforce_gallery_scope(request)
        access_session = self.get_access_session(request, gallery)

        allowed_visibility = [VisibilityChoices.PUBLIC]
        if access_session.role == GalleryAccessRole.CLIENT:
            allowed_visibility.append(VisibilityChoices.CLIENT_ONLY)

        photos_per_scene = int(
            getattr(settings, "GALLERY_PUBLIC_PHOTOS_PER_SCENE", 100)
        )
        photo_queryset = (
            Photo.objects
            .filter(
                scene__event=gallery,
                status="READY",
                visibility__in=allowed_visibility,
            )
            .alias(
                _scene_rn=Window(
                    expression=RowNumber(),
                    partition_by=[F("scene_id")],
                    order_by=[F("uploaded_at").asc(), F("id").asc()],
                )
            )
            .filter(_scene_rn__lte=photos_per_scene + 1)
            .select_related("scene__event__workspace")
            .order_by("uploaded_at", "id")
        )
        scene_queryset = (
            Scene.objects
            .filter(
                event=gallery,
                visibility__in=allowed_visibility,
            )
            .order_by("display_order", "title")
            .prefetch_related(Prefetch("photos", queryset=photo_queryset))
        )
        gallery = (
            Event.objects
            .filter(id=gallery.id)
            .prefetch_related(Prefetch("scenes", queryset=scene_queryset))
            .get()
        )

        serializer = GalleryPublicSerializer(
            gallery,
            context={
                "photos_per_scene_limit": photos_per_scene,
                "access_role": access_session.role,
                "allow_downloads": gallery.allow_downloads,
            },
        )
        return Response(
            {
                "gallery": serializer.data,
                "access": {
                    "gallery_id": str(request.user.gallery_id),
                    "share_code": gallery.share_code,
                    "email": request.user.email,
                    "role": request.user.role,
                },
            },
            status=status.HTTP_200_OK,
        )


class PublicScenePhotoListView(GallerySecurityHeadersMixin, PublishedGalleryMixin, APIView):
    """Nested keyset list when the detail Prefetch bound (100/scene) is exceeded."""

    authentication_classes = [GalleryCookieJWTAuthentication]
    permission_classes = [HasClientOrGuestGalleryAccess]
    throttle_classes = [GallerySessionReadThrottle]
    pagination_class = ClientScenePhotoKeysetPagination

    def get(self, request, *args, **kwargs):
        self.enforce_gallery_scope(request)
        gallery = self.get_gallery()
        access_session = self.get_access_session(request, gallery)

        scene = (
            Scene.objects
            .filter(id=self.kwargs["scene_id"], event=gallery)
            .first()
        )
        if not scene:
            raise NotFound("Scene not found.")

        allowed_visibility = [VisibilityChoices.PUBLIC]
        if access_session.role == GalleryAccessRole.CLIENT:
            allowed_visibility.append(VisibilityChoices.CLIENT_ONLY)

        if scene.visibility not in allowed_visibility:
            raise NotFound("Scene not found.")

        queryset = (
            Photo.objects
            .filter(
                scene=scene,
                status="READY",
                visibility__in=allowed_visibility,
            )
            .select_related("scene__event__workspace")
            .order_by("uploaded_at", "id")
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = GalleryPublicPhotoSerializer(
            page,
            many=True,
            context={
                "access_role": access_session.role,
                "allow_downloads": gallery.allow_downloads,
            },
        )
        return paginator.get_paginated_response(serializer.data)


class GalleryFavoriteSelectionView(GallerySecurityHeadersMixin, PublishedGalleryMixin, APIView):
    authentication_classes = [GalleryCookieJWTAuthentication]
    permission_classes = [HasClientOrGuestGalleryAccess]
    throttle_classes = [FavoriteSelectionThrottle]

    def post(self, request, *args, **kwargs):
        self.enforce_gallery_scope(request)
        gallery = self.get_gallery()
        access_session = self.get_access_session(request, gallery)
        serializer = FavoriteSelectionWriteSerializer(
            data=request.data,
            context={
                "gallery": gallery,
                "role": request.user.role,
            },
        )
        serializer.is_valid(raise_exception=True)

        selection, created = FavoriteSelection.objects.update_or_create(
            session=access_session,
            photo=serializer.validated_data["photo"],
            defaults={"notes": serializer.validated_data["notes"]},
        )

        response_serializer = FavoriteSelectionSerializer(selection)
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class GalleryFavoriteSelectionDetailView(GallerySecurityHeadersMixin, PublishedGalleryMixin, APIView):
    authentication_classes = [GalleryCookieJWTAuthentication]
    permission_classes = [HasClientOrGuestGalleryAccess]
    throttle_classes = [FavoriteSelectionThrottle]

    def delete(self, request, *args, **kwargs):
        self.enforce_gallery_scope(request)
        gallery = self.get_gallery()
        access_session = self.get_access_session(request, gallery)
        serializer = FavoriteSelectionWriteSerializer(
            data={"photo_id": self.kwargs["photo_id"]},
            context={
                "gallery": gallery,
                "role": request.user.role,
            },
        )
        serializer.is_valid(raise_exception=True)

        FavoriteSelection.objects.filter(
            session=access_session,
            photo=serializer.validated_data["photo"],
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class GalleryArchiveRequestView(GallerySecurityHeadersMixin, PublishedGalleryMixin, APIView):
    authentication_classes = [GalleryCookieJWTAuthentication]
    permission_classes = [HasClientGalleryAccess]

    def post(self, request, *args, **kwargs):
        self.enforce_gallery_scope(request)
        gallery = self.get_gallery()
        self.get_access_session(request, gallery)
        if not gallery.allow_downloads:
            raise PermissionDenied("Downloads are disabled for this gallery.")
        now = timezone.now()

        existing = (
            gallery.archive_jobs
            .filter(archive_type=GalleryArchiveType.FULL)
            .first()
        )
        if existing:
            existing = _maybe_resume_stale_archive_job(existing)
        if existing and existing.status in (
            GalleryArchiveJob.Status.PENDING,
            GalleryArchiveJob.Status.PROCESSING,
        ):
            return Response(
                {"archive_job_id": existing.id, "status": existing.status},
                status=status.HTTP_202_ACCEPTED,
            )

        if (
            existing
            and existing.status == GalleryArchiveJob.Status.COMPLETED
            and existing.r2_zip_key
            and existing.expires_at
            and existing.expires_at > now
        ):
            return Response(
                {"archive_job_id": existing.id, "status": existing.status},
                status=status.HTTP_202_ACCEPTED,
            )

        job = GalleryArchiveJob.objects.create(
            gallery=gallery,
            archive_type=GalleryArchiveType.FULL,
        )
        _enqueue_archive_job(job.id)
        return Response(
            {"archive_job_id": job.id, "status": job.status},
            status=status.HTTP_202_ACCEPTED,
        )


class GalleryArchiveStatusView(GallerySecurityHeadersMixin, PublishedGalleryMixin, APIView):
    authentication_classes = [GalleryCookieJWTAuthentication]
    permission_classes = [HasClientGalleryAccess]

    def get(self, request, *args, **kwargs):
        self.enforce_gallery_scope(request)
        gallery = self.get_gallery()
        self.get_access_session(request, gallery)
        if not gallery.allow_downloads:
            raise PermissionDenied("Downloads are disabled for this gallery.")
        job = (
            gallery.archive_jobs
            .filter(archive_type=GalleryArchiveType.FULL)
            .first()
        )
        if not job:
            raise NotFound("Archive job not found.")

        download_url = None
        if (
            job.status == GalleryArchiveJob.Status.COMPLETED
            and job.r2_zip_key
            and job.expires_at
            and job.expires_at > timezone.now()
        ):
            download_url = generate_r2_presigned_get_url(
                bucket=getattr(settings, "CLOUDFLARE_R2_BUCKET_NAME", ""),
                key=job.r2_zip_key,
                expires_in=60,
            )

        return Response(
            {
                "archive_job_id": job.id,
                "status": job.status,
                "download_url": download_url,
                "expires_at": job.expires_at,
            },
            status=status.HTTP_200_OK,
        )


class GalleryFavoritesArchiveRequestView(GallerySecurityHeadersMixin, PublishedGalleryMixin, APIView):
    authentication_classes = [GalleryCookieJWTAuthentication]
    permission_classes = [HasClientOrGuestGalleryAccess]

    def post(self, request, *args, **kwargs):
        self.enforce_gallery_scope(request)
        gallery = self.get_gallery()
        access_session = self.get_access_session(request, gallery)
        if not gallery.allow_downloads:
            raise PermissionDenied("Downloads are disabled for this gallery.")
        now = timezone.now()

        has_favorites = FavoriteSelection.objects.filter(session=access_session).exists()
        if not has_favorites:
            raise PermissionDenied("No favorites selected for this session.")

        existing = (
            gallery.archive_jobs
            .filter(
                archive_type=GalleryArchiveType.FAVORITES,
                access_session=access_session,
            )
            .first()
        )
        if existing:
            existing = _maybe_resume_stale_archive_job(existing)
        if existing and existing.status in (
            GalleryArchiveJob.Status.PENDING,
            GalleryArchiveJob.Status.PROCESSING,
        ):
            return Response(
                {"archive_job_id": existing.id, "status": existing.status},
                status=status.HTTP_202_ACCEPTED,
            )

        if (
            existing
            and existing.status == GalleryArchiveJob.Status.COMPLETED
            and existing.r2_zip_key
            and existing.expires_at
            and existing.expires_at > now
        ):
            return Response(
                {"archive_job_id": existing.id, "status": existing.status},
                status=status.HTTP_202_ACCEPTED,
            )

        job = GalleryArchiveJob.objects.create(
            gallery=gallery,
            access_session=access_session,
            archive_type=GalleryArchiveType.FAVORITES,
        )
        _enqueue_archive_job(job.id)
        return Response(
            {"archive_job_id": job.id, "status": job.status},
            status=status.HTTP_202_ACCEPTED,
        )


class GalleryFavoritesArchiveStatusView(GallerySecurityHeadersMixin, PublishedGalleryMixin, APIView):
    authentication_classes = [GalleryCookieJWTAuthentication]
    permission_classes = [HasClientOrGuestGalleryAccess]

    def get(self, request, *args, **kwargs):
        self.enforce_gallery_scope(request)
        gallery = self.get_gallery()
        access_session = self.get_access_session(request, gallery)
        if not gallery.allow_downloads:
            raise PermissionDenied("Downloads are disabled for this gallery.")
        job = (
            gallery.archive_jobs
            .filter(
                archive_type=GalleryArchiveType.FAVORITES,
                access_session=access_session,
            )
            .first()
        )
        if not job:
            raise NotFound("Archive job not found.")

        download_url = None
        if (
            job.status == GalleryArchiveJob.Status.COMPLETED
            and job.r2_zip_key
            and job.expires_at
            and job.expires_at > timezone.now()
        ):
            download_url = generate_r2_presigned_get_url(
                bucket=getattr(settings, "CLOUDFLARE_R2_BUCKET_NAME", ""),
                key=job.r2_zip_key,
                expires_in=60,
            )

        return Response(
            {
                "archive_job_id": job.id,
                "status": job.status,
                "download_url": download_url,
                "expires_at": job.expires_at,
            },
            status=status.HTTP_200_OK,
        )
