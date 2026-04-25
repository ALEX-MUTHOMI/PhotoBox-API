import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from gallery.client_auth import (
    GalleryCookieJWTAuthentication,
    get_gallery_access_session_id,
    hash_magic_link_token,
    issue_gallery_access_token,
    normalize_gallery_email,
    set_gallery_access_cookie,
    set_gallery_access_session_cookie,
)
from gallery.client_permissions import (
    HasClientGalleryAccess,
    HasClientOrGuestGalleryAccess,
)
from gallery.client_serializers import (
    FavoriteSelectionSerializer,
    FavoriteSelectionWriteSerializer,
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
from gallery.storage import generate_r2_presigned_get_url
from gallery.tasks import build_gallery_archive
from gallery.throttles import (
    FavoriteSelectionThrottle,
    GuestAccessThrottle,
    MagicLinkSendThrottle,
)


class PublishedGalleryMixin:
    def get_gallery(self):
        gallery = Event.objects.filter(
            id=self.kwargs["gallery_id"],
            is_published=True,
        ).first()
        if not gallery:
            raise NotFound("Gallery not found.")

        if gallery.expires_at and gallery.expires_at <= timezone.now():
            raise NotFound("Gallery not found.")

        return gallery

    def enforce_gallery_scope(self, request):
        token_gallery_id = str((request.auth or {}).get("gallery_id", ""))
        requested_gallery_id = str(self.kwargs["gallery_id"])
        if token_gallery_id != requested_gallery_id:
            raise PermissionDenied("Gallery scope mismatch.")

    def get_access_session(self, request, gallery):
        session_id = get_gallery_access_session_id(request)
        if session_id is None:
            raise PermissionDenied("Gallery session missing.")

        session = (
            GalleryAccessSession.objects
            .filter(
                id=session_id,
                gallery=gallery,
                email=normalize_gallery_email(getattr(request.user, "email", "")),
                role=getattr(request.user, "role", ""),
            )
            .first()
        )
        if not session:
            raise PermissionDenied("Gallery session invalid.")

        return session


class GalleryMagicLinkRequestView(PublishedGalleryMixin, APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [MagicLinkSendThrottle]

    def post(self, request, *args, **kwargs):
        gallery = self.get_gallery()
        serializer = MagicLinkRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        if ClientAllowlist.objects.filter(gallery=gallery, email=email).exists():
            raw_token = secrets.token_urlsafe(64)
            GalleryMagicLink.objects.filter(gallery=gallery, email=email).delete()
            GalleryMagicLink.objects.create(
                gallery=gallery,
                email=email,
                token_hash=hash_magic_link_token(raw_token),
                expires_at=timezone.now() + timedelta(minutes=15),
            )
            frontend_url = getattr(settings, "FRONTEND_URL", "https://app.photobox.app").rstrip("/")
            magic_url = f"{frontend_url}/gallery-access?token={raw_token}"
            send_mail(
                subject=f"Your secure access link for {gallery.title}",
                message=(
                    f"Use this single-use link within 15 minutes to access {gallery.title}: "
                    f"{magic_url}"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )

        return Response(
            {"detail": "If that address is approved, a magic link will be sent."},
            status=status.HTTP_202_ACCEPTED,
        )


class GalleryMagicLinkConsumeView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = MagicLinkConsumeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_token = serializer.validated_data["token"]
        token_hash = hash_magic_link_token(raw_token)

        with transaction.atomic():
            magic_link = (
                GalleryMagicLink.objects
                .select_for_update()
                .select_related("gallery")
                .filter(token_hash=token_hash)
                .first()
            )
            if not magic_link:
                raise PermissionDenied("Invalid or already-used magic link.")

            if magic_link.expires_at <= timezone.now():
                magic_link.delete()
                raise PermissionDenied("Magic link expired.")

            session = GalleryAccessSession.objects.create(
                gallery=magic_link.gallery,
                email=magic_link.email,
                role=GalleryAccessRole.CLIENT,
            )
            token = issue_gallery_access_token(
                gallery_id=magic_link.gallery_id,
                email=magic_link.email,
                role=GalleryAccessRole.CLIENT,
            )
            gallery_id = str(magic_link.gallery_id)
            email = magic_link.email
            magic_link.delete()

        response = Response(
            {
                "authenticated": True,
                "gallery_id": gallery_id,
                "email": email,
                "role": GalleryAccessRole.CLIENT,
                "session_id": session.id,
            },
            status=status.HTTP_200_OK,
        )
        set_gallery_access_cookie(response, token)
        return set_gallery_access_session_cookie(response, session.id)


class GalleryGuestAccessView(PublishedGalleryMixin, APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [GuestAccessThrottle]

    def post(self, request, *args, **kwargs):
        gallery = self.get_gallery()
        serializer = GuestAccessSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        session = GalleryAccessSession.objects.create(
            gallery=gallery,
            email=email,
            role=GalleryAccessRole.GUEST,
        )
        token = issue_gallery_access_token(
            gallery_id=gallery.id,
            email=email,
            role=GalleryAccessRole.GUEST,
        )
        response = Response(
            {
                "authenticated": True,
                "gallery_id": str(gallery.id),
                "email": email,
                "role": GalleryAccessRole.GUEST,
                "session_id": session.id,
            },
            status=status.HTTP_200_OK,
        )
        set_gallery_access_cookie(response, token)
        return set_gallery_access_session_cookie(response, session.id)


class PublicGalleryDetailView(PublishedGalleryMixin, APIView):
    authentication_classes = [GalleryCookieJWTAuthentication]
    permission_classes = [HasClientOrGuestGalleryAccess]

    def get(self, request, *args, **kwargs):
        self.enforce_gallery_scope(request)
        gallery = self.get_gallery()

        allowed_visibility = [VisibilityChoices.PUBLIC]
        if request.user.role == GalleryAccessRole.CLIENT:
            allowed_visibility.append(VisibilityChoices.CLIENT_ONLY)

        photo_queryset = (
            Photo.objects
            .filter(
                status="READY",
                visibility__in=allowed_visibility,
            )
            .order_by("uploaded_at")
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

        serializer = GalleryPublicSerializer(gallery)
        return Response(
            {
                "gallery": serializer.data,
                "access": {
                    "gallery_id": str(request.user.gallery_id),
                    "email": request.user.email,
                    "role": request.user.role,
                },
            },
            status=status.HTTP_200_OK,
        )


class GalleryFavoriteSelectionView(PublishedGalleryMixin, APIView):
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


class GalleryFavoriteSelectionDetailView(PublishedGalleryMixin, APIView):
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


class GalleryArchiveRequestView(PublishedGalleryMixin, APIView):
    authentication_classes = [GalleryCookieJWTAuthentication]
    permission_classes = [HasClientGalleryAccess]

    def post(self, request, *args, **kwargs):
        self.enforce_gallery_scope(request)
        gallery = self.get_gallery()
        now = timezone.now()

        existing = (
            gallery.archive_jobs
            .filter(archive_type=GalleryArchiveType.FULL)
            .first()
        )
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
        build_gallery_archive.delay(str(job.id))
        return Response(
            {"archive_job_id": job.id, "status": job.status},
            status=status.HTTP_202_ACCEPTED,
        )


class GalleryArchiveStatusView(PublishedGalleryMixin, APIView):
    authentication_classes = [GalleryCookieJWTAuthentication]
    permission_classes = [HasClientGalleryAccess]

    def get(self, request, *args, **kwargs):
        self.enforce_gallery_scope(request)
        gallery = self.get_gallery()
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


class GalleryFavoritesArchiveRequestView(PublishedGalleryMixin, APIView):
    authentication_classes = [GalleryCookieJWTAuthentication]
    permission_classes = [HasClientOrGuestGalleryAccess]

    def post(self, request, *args, **kwargs):
        self.enforce_gallery_scope(request)
        gallery = self.get_gallery()
        access_session = self.get_access_session(request, gallery)
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
        build_gallery_archive.delay(str(job.id))
        return Response(
            {"archive_job_id": job.id, "status": job.status},
            status=status.HTTP_202_ACCEPTED,
        )


class GalleryFavoritesArchiveStatusView(PublishedGalleryMixin, APIView):
    authentication_classes = [GalleryCookieJWTAuthentication]
    permission_classes = [HasClientOrGuestGalleryAccess]

    def get(self, request, *args, **kwargs):
        self.enforce_gallery_scope(request)
        gallery = self.get_gallery()
        access_session = self.get_access_session(request, gallery)
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
