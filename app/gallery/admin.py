"""
Django admin customization for PhotoBox SaaS (Gallery Resources).
"""
from django.contrib import admin
from .models import Event, Scene, Photo

class SceneInline(admin.TabularInline):
    """Allows admins to see Scenes directly inside the Event page."""
    model = Scene
    extra = 0
    fields = ['title', 'display_order']

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    """Define the admin pages for Events."""
    list_display = ['title', 'workspace', 'event_type', 'slug', 'is_published', 'created_at']
    search_fields = ['title', 'slug', 'workspace__business_name']
    list_filter = ['is_published', 'event_type']
    readonly_fields = ['id', 'created_at']
    ordering = ['-created_at']
    inlines = [SceneInline]

class PhotoInline(admin.TabularInline):
    """Allows admins to see Photos directly inside the Scene page."""
    model = Photo
    extra = 0
    fields = ['original_filename', 'file_size_bytes', 'status', 'is_processed', 'r2_object_key']
    readonly_fields = ['uploaded_at']

@admin.register(Scene)
class SceneAdmin(admin.ModelAdmin):
    """Define the admin pages for individual Scenes."""
    list_display = ['id', 'title', 'event', 'display_order']
    search_fields = ['title', 'event__title']
    ordering = ['event', 'display_order']
    inlines = [PhotoInline]

@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    """Define the admin pages for individual Photos."""
    list_display = ['id', 'original_filename', 'scene', 'status', 'file_size_bytes', 'is_processed']
    search_fields = ['original_filename', 'scene__title']
    list_filter = ['status', 'is_processed']
    readonly_fields = ['id', 'uploaded_at', 'file_size_bytes', 'r2_object_key', 'optimized_url', 'blurhash']
    ordering = ['-uploaded_at']
