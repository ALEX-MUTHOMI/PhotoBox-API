"""
Django admin customization for PhotoBox SaaS (Core Identity).
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from core import models
from billing.models import Subscription  # <--- ENGINEER FIX 1: Import the Billing table

class SubscriptionInline(admin.StackedInline):
    """
    ENGINEER FIX 2: Create a secure bridge.
    This injects the billing data into the user profile without crashing the database.
    """
    model = Subscription
    can_delete = False
    verbose_name_plural = 'Lemon Squeezy Billing Profile'
    readonly_fields = [
        'is_pro',
        'storage_limit_bytes',
        'storage_used_bytes',
        'lemon_squeezy_customer_id',
        'lemon_squeezy_subscription_id'
    ]
    fields = readonly_fields

class UserAdmin(BaseUserAdmin):
    """Define the admin pages for users."""

    # ENGINEER FIX 3: Removed missing billing fields. Only using columns that actually exist on User.
    search_fields = ['email', 'name']
    list_filter = ['is_active', 'is_staff']
    ordering = ['email']
    list_display = ['email', 'name', 'storage_limit_gb']

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        (_('Personal Info'), {'fields': ('name',)}),
        (_('SaaS Data'), {
            'fields': (
                'storage_limit_gb',
                # Assuming accepted_terms exists on your User model based on earlier migrations
                'accepted_terms'
            )
        }),
        (
            _('Permissions'),
            {
                'fields': (
                    'is_active',
                    'is_staff',
                    'is_superuser',
                )
            }
        ),
        (_('Important dates'), {'fields': ('last_login',)}),
    )

    readonly_fields = ['last_login']

    # ENGINEER FIX 4: Activate the bridge. This displays the SubscriptionInline we built above.
    inlines = [SubscriptionInline]

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'email',
                'password1',
                'password2',
                'name',
                'storage_limit_gb',
                'is_active',
                'is_staff',
                'is_superuser',
            ),
        }),
    )

# Register the core models
admin.site.register(models.User, UserAdmin)












# """
# Django admin customization for PhotoBox SaaS.
# """
# from django.contrib import admin
# from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
# from django.utils.translation import gettext_lazy as _

# from core import models


# class UserAdmin(BaseUserAdmin):
#     """Define the admin pages for users."""
#     search_fields = ['email', 'name', 'stripe_customer_id']
#     list_filter = ['subscription_tier', 'is_active', 'is_staff']
#     ordering = ['email']
#     list_display = ['email', 'name', 'subscription_tier', 'storage_limit_gb']

#     fieldsets = (
#         (None, {'fields': ('email', 'password')}),
#         (_('Personal Info'), {'fields': ('name',)}),
#         (_('SaaS Billing'), {
#             'fields': (
#                 'stripe_customer_id',
#                 'subscription_tier',
#                 'storage_limit_gb'
#             )
#         }),
#         (
#             _('Permissions'),
#             {
#                 'fields': (
#                     'is_active',
#                     'is_staff',
#                     'is_superuser',
#                 )
#             }
#         ),
#         (_('Important dates'), {'fields': ('last_login',)}),
#     )

#     readonly_fields = ['last_login', 'stripe_customer_id']

#     add_fieldsets = (
#         (None, {
#             'classes': ('wide',),
#             'fields': (
#                 'email',
#                 'password1',
#                 'password2',
#                 'name',
#                 'subscription_tier',
#                 'storage_limit_gb',
#                 'is_active',
#                 'is_staff',
#                 'is_superuser',
#             ),
#         }),
#     )


# # --- SAAS RESOURCE ADMINS ---

# class GalleryInline(admin.TabularInline):
#     """Allows admins to see Galleries directly inside the Workspace page."""
#     model = models.Gallery
#     extra = 0
#     fields = ['title', 'is_public', 'allow_downloads', 'is_deleted', 'created_at']
#     readonly_fields = ['created_at']


# class WorkspaceAdmin(admin.ModelAdmin):
#     """Define the admin pages for photographer workspaces."""
#     list_display = ['business_name', 'user', 'custom_domain', 'is_deleted', 'created_at']
#     search_fields = ['business_name', 'custom_domain', 'user__email']
#     list_filter = ['is_deleted']
#     readonly_fields = ['id', 'created_at', 'updated_at']
#     ordering = ['-created_at']
#     inlines = [GalleryInline] # Injects the galleries into the workspace view


# class ImageInline(admin.TabularInline):
#     """Allows admins to see Images directly inside the Gallery page."""
#     model = models.Image
#     extra = 0
#     fields = ['title', 'order', 'is_deleted', 'created_at']
#     readonly_fields = ['created_at']


# class GalleryAdmin(admin.ModelAdmin):
#     """Define the admin pages for client galleries."""
#     list_display = ['title', 'workspace', 'is_public', 'allow_downloads', 'is_deleted']
#     search_fields = ['title', 'slug', 'workspace__business_name']
#     list_filter = ['is_deleted', 'is_public', 'allow_downloads']
#     readonly_fields = ['id', 'created_at', 'updated_at']
#     inlines = [ImageInline] # Injects the images into the gallery view


# class ImageAdmin(admin.ModelAdmin):
#     """Define the admin pages for individual images."""
#     list_display = ['id', 'title', 'gallery', 'order', 'is_deleted']
#     search_fields = ['title', 'gallery__title']
#     list_filter = ['is_deleted']
#     readonly_fields = ['id', 'created_at', 'updated_at']
#     ordering = ['gallery', 'order']


# # Register the models to the admin site
# admin.site.register(models.User, UserAdmin)
# admin.site.register(models.Workspace, WorkspaceAdmin)
# admin.site.register(models.Gallery, GalleryAdmin)
# admin.site.register(models.Image, ImageAdmin)
