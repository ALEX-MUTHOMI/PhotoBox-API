"""
Django admin customization for PhotoBox SaaS.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from core import models


class UserAdmin(BaseUserAdmin):
    """Define the admin pages for users."""
    ordering = ['id']
    list_display = ['email', 'name', 'subscription_tier']

    # This controls the "Edit User" page layout
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
    readonly_fields = ['last_login']

    # This controls the "Create User" page layout
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


# Register only PhotoBox models, drop all legacy course models
admin.site.register(models.User, UserAdmin)
admin.site.register(models.Workspace)


















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
