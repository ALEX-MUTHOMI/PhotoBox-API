"""
Django admin customization for PhotoBox SaaS.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from core import models


class UserAdmin(BaseUserAdmin):
    """Define the admin pages for users."""

    # SAAS UPGRADE: Search and filter tools for customer support
    search_fields = ['email', 'name', 'stripe_customer_id']
    list_filter = ['subscription_tier', 'is_active', 'is_staff']

    # Order by email since we use UUIDs now
    ordering = ['email']
    list_display = ['email', 'name', 'subscription_tier', 'storage_limit_gb']

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        (_('Personal Info'), {'fields': ('name',)}),
        (_('SaaS Billing'), {
            'fields': (
                'stripe_customer_id',
                'subscription_tier',
                'storage_limit_gb'
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

    # SAAS UPGRADE: Lock the Stripe ID so admins cannot accidentally break billing
    readonly_fields = ['last_login', 'stripe_customer_id']

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'email',
                'password1',
                'password2',
                'name',
                'subscription_tier',
                'storage_limit_gb',
                'is_active',
                'is_staff',
                'is_superuser',
            ),
        }),
    )


class WorkspaceAdmin(admin.ModelAdmin):
    """Define the admin pages for photographer workspaces."""
    list_display = ['business_name', 'user', 'custom_domain', 'created_at']
    search_fields = ['business_name', 'custom_domain', 'user__email']
    readonly_fields = ['id', 'created_at', 'updated_at']
    ordering = ['-created_at']


admin.site.register(models.User, UserAdmin)
admin.site.register(models.Workspace, WorkspaceAdmin)







# """
# Django admin customization.
# """
# from django.contrib import admin
# from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
# from django.utils.translation import gettext_lazy as _

# from core import models


# class UserAdmin(BaseUserAdmin):
#     """Define the admin pages for users."""
#     ordering = ['id']
#     list_display = ['email', 'name']
#     fieldsets = (
#         (None, {'fields': ('email', 'password')}),
#         (_('Personal Info'), {'fields': ('name',)}),
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
#     readonly_fields = ['last_login']
#     add_fieldsets = (
#         (None, {
#             'classes': ('wide',),
#             'fields': (
#                 'email',
#                 'password1',
#                 'password2',
#                 'name',
#                 'is_active',
#                 'is_staff',
#                 'is_superuser',
#             ),
#         }),
#     )


# admin.site.register(models.User, UserAdmin)
# admin.site.register(models.Recipe)
# admin.site.register(models.Tag)
# admin.site.register(models.Ingredient)
