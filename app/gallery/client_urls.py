"""URL routes for the unauthenticated and client-scoped public gallery API."""

from django.urls import path

from gallery import client_views, views as gallery_views

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
        "<uuid:gallery_id>/favorites/",
        client_views.GalleryFavoriteSelectionView.as_view(),
        name="favorites",
    ),
    path(
        "<uuid:gallery_id>/favorites/<uuid:photo_id>/",
        client_views.GalleryFavoriteSelectionDetailView.as_view(),
        name="favorite-detail",
    ),
    path(
        "<uuid:gallery_id>/favorites-summary/",
        gallery_views.PhotographerFavoritesSummaryView.as_view(),
        name="favorites-summary",
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
        "<uuid:gallery_id>/archive/favorites/",
        client_views.GalleryFavoritesArchiveRequestView.as_view(),
        name="favorites-archive-request",
    ),
    path(
        "<uuid:gallery_id>/archive/favorites/status/",
        client_views.GalleryFavoritesArchiveStatusView.as_view(),
        name="favorites-archive-status",
    ),
    path(
        "<uuid:gallery_id>/",
        client_views.PublicGalleryDetailView.as_view(),
        name="detail",
    ),
]
