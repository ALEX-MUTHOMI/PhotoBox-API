"""
Django admin customization for PhotoBox SaaS (Gallery Resources).
"""
from django.contrib import admin
from .models import (
    ClientAllowlist,
    Event,
    FavoriteSelection,
    GalleryAccessSession,
    GalleryArchiveJob,
    GalleryMagicLink,
    Photo,
    Scene,
)


class SceneInline(admin.TabularInline):
    """Allows admins to see Scenes directly inside the Event page."""
    model = Scene
    extra = 0
    fields = ['title', 'display_order']

class PhotoInline(admin.TabularInline):
    """Allows admins to see Photos directly inside the Scene page."""
    model = Photo
    extra = 0
    fields = ['original_filename', 'visibility', 'file_size_bytes', 'status', 'is_processed', 'r2_object_key']
    readonly_fields = ['uploaded_at']


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    """Define the admin pages for Events."""
    list_display = ['title', 'workspace', 'event_type', 'slug', 'typography_theme', 'color_theme', 'is_published', 'created_at']
    search_fields = ['title', 'slug', 'workspace__business_name']
    list_filter = ['is_published', 'event_type']
    readonly_fields = ['id', 'created_at']
    ordering = ['-created_at']
    inlines = [SceneInline]
    
    # SCALE FIX: Prevents N+1 query spam when loading the Event list UI.
    # Fetches the workspace in the same SQL join.
    list_select_related = ['workspace']


@admin.register(Scene)
class SceneAdmin(admin.ModelAdmin):
    """Define the admin pages for individual Scenes."""
    list_display = ['id', 'title', 'event', 'visibility', 'display_order']
    search_fields = ['title', 'event__title']
    ordering = ['event', 'display_order']
    inlines = [PhotoInline]
    
    # SCALE FIX: Avoid N+1 queries for the 'event' column.
    list_select_related = ['event']
    # SCALE FIX: Replaces the default HTML <select> dropdown with a searchable 
    # AJAX input. If you have 50,000 events, a standard dropdown will crash the browser.
    autocomplete_fields = ['event']


@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    """Define the admin pages for individual Photos."""
    list_display = ['id', 'original_filename', 'scene', 'visibility', 'status', 'file_size_bytes', 'is_processed']
    search_fields = ['original_filename', 'scene__title']
    list_filter = ['visibility', 'status', 'is_processed']
    readonly_fields = ['id', 'uploaded_at', 'file_size_bytes', 'r2_object_key', 'web_r2_object_key', 'optimized_url', 'blurhash']
    ordering = ['-uploaded_at']
    
    # SCALE FIXES
    list_select_related = ['scene']
    autocomplete_fields = ['scene']


@admin.register(GalleryArchiveJob)
class GalleryArchiveJobAdmin(admin.ModelAdmin):
    list_display = ['id', 'gallery', 'status', 'expires_at', 'created_at']
    search_fields = ['gallery__title', 'gallery__slug', 'r2_zip_key']
    list_filter = ['status']
    readonly_fields = ['created_at', 'updated_at']
    list_select_related = ['gallery']


@admin.register(GalleryAccessSession)
class GalleryAccessSessionAdmin(admin.ModelAdmin):
    list_display = ['id', 'gallery', 'email', 'role', 'created_at']
    search_fields = ['email', 'gallery__title', 'gallery__slug']
    list_filter = ['role']
    readonly_fields = ['created_at']
    list_select_related = ['gallery']


@admin.register(FavoriteSelection)
class FavoriteSelectionAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'photo', 'created_at']
    search_fields = ['session__email', 'photo__original_filename', 'session__gallery__title']
    readonly_fields = ['created_at']
    list_select_related = ['session', 'photo']


@admin.register(ClientAllowlist)
class ClientAllowlistAdmin(admin.ModelAdmin):
    list_display = ['id', 'gallery', 'email', 'created_at']
    search_fields = ['email', 'gallery__title', 'gallery__slug']
    readonly_fields = ['created_at']
    list_select_related = ['gallery']


@admin.register(GalleryMagicLink)
class GalleryMagicLinkAdmin(admin.ModelAdmin):
    list_display = ['id', 'gallery', 'email', 'expires_at', 'created_at']
    search_fields = ['email', 'gallery__title', 'gallery__slug']
    readonly_fields = ['token_hash', 'created_at']
    list_select_related = ['gallery']








# """
# Django admin customization for PhotoBox SaaS (Gallery Resources).
# """
# from django.contrib import admin
# from .models import Event, Scene, Photo

# class SceneInline(admin.TabularInline):
#     """Allows admins to see Scenes directly inside the Event page."""
#     model = Scene
#     extra = 0
#     fields = ['title', 'display_order']

# @admin.register(Event)          # produces admin:gallery_event_*
# class EventAdmin(admin.ModelAdmin):
#     inlines = [SceneInline]

# @admin.register(Photo)          # produces admin:gallery_photo_*
# class PhotoAdmin(admin.ModelAdmin):
#     pass

# @admin.register(Event)
# class EventAdmin(admin.ModelAdmin):
#     """Define the admin pages for Events."""
#     list_display = ['title', 'workspace', 'event_type', 'slug', 'is_published', 'created_at']
#     search_fields = ['title', 'slug', 'workspace__business_name']
#     list_filter = ['is_published', 'event_type']
#     readonly_fields = ['id', 'created_at']
#     ordering = ['-created_at']
#     inlines = [SceneInline]

# class PhotoInline(admin.TabularInline):
#     """Allows admins to see Photos directly inside the Scene page."""
#     model = Photo
#     extra = 0
#     fields = ['original_filename', 'file_size_bytes', 'status', 'is_processed', 'r2_object_key']
#     readonly_fields = ['uploaded_at']

# @admin.register(Scene)
# class SceneAdmin(admin.ModelAdmin):
#     """Define the admin pages for individual Scenes."""
#     list_display = ['id', 'title', 'event', 'display_order']
#     search_fields = ['title', 'event__title']
#     ordering = ['event', 'display_order']
#     inlines = [PhotoInline]

# @admin.register(Photo)
# class PhotoAdmin(admin.ModelAdmin):
#     """Define the admin pages for individual Photos."""
#     list_display = ['id', 'original_filename', 'scene', 'status', 'file_size_bytes', 'is_processed']
#     search_fields = ['original_filename', 'scene__title']
#     list_filter = ['status', 'is_processed']
#     readonly_fields = ['id', 'uploaded_at', 'file_size_bytes', 'r2_object_key', 'optimized_url', 'blurhash']
#     ordering = ['-uploaded_at']
