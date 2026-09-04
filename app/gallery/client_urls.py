"""URL routes for the unauthenticated and client-scoped public gallery API."""

from django.urls import path

from gallery import client_views, views as gallery_views

app_name = "gallery_public"

urlpatterns = [
    # ------------------------------------------------------------------
    # Share-code public surface (Pixieset-style). UUID is not a public door.
    # ------------------------------------------------------------------
    path(
        "g/<str:share_code>/magic-link/",
        client_views.GalleryMagicLinkRequestView.as_view(),
        name="magic-link-request",
    ),
    path(
        "magic-link/consume/",
        client_views.GalleryMagicLinkConsumeView.as_view(),
        name="magic-link-consume",
    ),
    path(
        "g/<str:share_code>/guest-access/",
        client_views.GalleryGuestAccessView.as_view(),
        name="guest-access",
    ),
    path(
        "g/<str:share_code>/guest-logout/",
        client_views.GalleryGuestLogoutView.as_view(),
        name="guest-logout",
    ),
    path(
        "g/<str:share_code>/favorites/",
        client_views.GalleryFavoriteSelectionView.as_view(),
        name="favorites",
    ),
    path(
        "g/<str:share_code>/favorites/<uuid:photo_id>/",
        client_views.GalleryFavoriteSelectionDetailView.as_view(),
        name="favorite-detail",
    ),
    path(
        "g/<str:share_code>/archive/",
        client_views.GalleryArchiveRequestView.as_view(),
        name="archive-request",
    ),
    path(
        "g/<str:share_code>/archive/status/",
        client_views.GalleryArchiveStatusView.as_view(),
        name="archive-status",
    ),
    path(
        "g/<str:share_code>/archive/favorites/",
        client_views.GalleryFavoritesArchiveRequestView.as_view(),
        name="favorites-archive-request",
    ),
    path(
        "g/<str:share_code>/archive/favorites/status/",
        client_views.GalleryFavoritesArchiveStatusView.as_view(),
        name="favorites-archive-status",
    ),
    path(
        "g/<str:share_code>/scenes/<uuid:scene_id>/photos/",
        client_views.PublicScenePhotoListView.as_view(),
        name="scene-photos",
    ),
    path(
        "g/<str:share_code>/",
        client_views.PublicGalleryDetailView.as_view(),
        name="detail",
    ),
    # Photographer-facing summary still keyed by gallery UUID (JWT owner APIs).
    path(
        "<uuid:gallery_id>/favorites-summary/",
        gallery_views.PhotographerFavoritesSummaryView.as_view(),
        name="favorites-summary",
    ),
    # Explicit UUID trap: anonymous/public detail must not work via Event.id.
    path(
        "<uuid:gallery_id>/",
        client_views.AnonymousUuidGalleryTrapView.as_view(),
        name="detail-uuid-trap",
    ),
]
