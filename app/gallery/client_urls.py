from django.urls import path

from gallery import client_views

app_name = "gallery_public"

urlpatterns = [
    path(
        "<uuid:gallery_id>/magic-link/",
        client_views.GalleryMagicLinkRequestView.as_view(),
        name="magic-link-request",
    ),
    path(
        "magic-link/consume/",
        client_views.GalleryMagicLinkConsumeView.as_view(),
        name="magic-link-consume",
    ),
    path(
        "<uuid:gallery_id>/guest-access/",
        client_views.GalleryGuestAccessView.as_view(),
        name="guest-access",
    ),
    path(
        "<uuid:gallery_id>/archive/",
        client_views.GalleryArchiveRequestView.as_view(),
        name="archive-request",
    ),
    path(
        "<uuid:gallery_id>/archive/status/",
        client_views.GalleryArchiveStatusView.as_view(),
        name="archive-status",
    ),
    path(
        "<uuid:gallery_id>/",
        client_views.PublicGalleryDetailView.as_view(),
        name="detail",
    ),
]
