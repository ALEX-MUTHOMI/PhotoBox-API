"""
Django admin customization for PhotoBox SaaS (Gallery Resources).
"""
from django.contrib import admin
from core import models# <--- ENGINEER FIX: Isolated to the gallery app

class GalleryInline(admin.TabularInline):
    """Allows admins to see Galleries directly inside the Workspace page."""
    model = models.Gallery
    extra = 0
    fields = ['title', 'is_public', 'allow_downloads', 'is_deleted', 'created_at']
    readonly_fields = ['created_at']

class WorkspaceAdmin(admin.ModelAdmin):
    """Define the admin pages for photographer workspaces."""
    list_display = ['business_name', 'user', 'custom_domain', 'is_deleted', 'created_at']
    search_fields = ['business_name', 'custom_domain', 'user__email']
    list_filter = ['is_deleted']
    readonly_fields = ['id', 'created_at', 'updated_at']
    ordering = ['-created_at']
    inlines = [GalleryInline] # Injects the galleries into the workspace view

class ImageInline(admin.TabularInline):
    """Allows admins to see Images directly inside the Gallery page."""
    model = models.Image
    extra = 0
    fields = ['title', 'order', 'is_deleted', 'created_at']
    readonly_fields = ['created_at']

class GalleryAdmin(admin.ModelAdmin):
    """Define the admin pages for client galleries."""
    list_display = ['title', 'workspace', 'is_public', 'allow_downloads', 'is_deleted']
    search_fields = ['title', 'slug', 'workspace__business_name']
    list_filter = ['is_deleted', 'is_public', 'allow_downloads']
    readonly_fields = ['id', 'created_at', 'updated_at']
    inlines = [ImageInline] # Injects the images into the gallery view

class ImageAdmin(admin.ModelAdmin):
    """Define the admin pages for individual images."""
    list_display = ['id', 'title', 'gallery', 'order', 'is_deleted']
    search_fields = ['title', 'gallery__title']
    list_filter = ['is_deleted']
    readonly_fields = ['id', 'created_at', 'updated_at']
    ordering = ['gallery', 'order']

# Register the gallery models
admin.site.register(models.Workspace, WorkspaceAdmin)
admin.site.register(models.Gallery, GalleryAdmin)
admin.site.register(models.Image, ImageAdmin)
