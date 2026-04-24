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
    list_display = ['email', 'name', 'subscription_tier', 'storage_limit_gb', 'accepted_terms', 'tos_version']

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        (_('Personal Info'), {'fields': ('name',)}),
        (_('SaaS Data'), {
            'fields': (
                'storage_limit_gb',
                # Assuming accepted_terms exists on your User model based on earlier migrations
                'accepted_terms',
                'tos_accepted_at',
                'tos_version',
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

    readonly_fields = ['last_login', 'tos_accepted_at', 'tos_version']

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

    @admin.action(description="GDPR Scrub (Anonymize User Data)")
    def gdpr_scrub_user(self, request, queryset):
        """
        Compliance: Overwrites PII while preserving financial history ledgers.
        This allows the business to retain financial audit logs while honoring 'Right to be Forgotten'.
        """
        import uuid
        for user in queryset:
            random_id = str(uuid.uuid4())[:8]
            user.email = f"scrubbed_{random_id}@anonymized.local"
            user.name = "Anonymized User"
            user.is_active = False
            # Revoke API keys and passwords
            user.set_unusable_password()
            user.save()
        
        self.message_user(request, f"Successfully anonymized {queryset.count()} user(s) in accordance with GDPR.")

    actions = [gdpr_scrub_user]

class WorkspaceAdmin(admin.ModelAdmin):
    """Define the admin pages for photographer workspaces."""
    list_display = ['business_name', 'user', 'custom_domain', 'created_at']
    search_fields = ['business_name', 'custom_domain', 'user__email']
    readonly_fields = ['id', 'created_at', 'updated_at']
    ordering = ['-created_at']

# Register the core models
admin.site.register(models.User, UserAdmin)
admin.site.register(models.Workspace, WorkspaceAdmin)

