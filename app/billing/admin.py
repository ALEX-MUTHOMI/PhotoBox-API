from django.contrib import admin
from .models import Subscription, ProcessedWebhook, BillingAuditLog, DeadLetterQueue

@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'is_pro', 'storage_limit_bytes', 'storage_used_bytes')
    search_fields = ('user__email', 'lemon_squeezy_customer_id')
    # ENGINEER FIX: Absolute Admin Handcuffs
    readonly_fields = ('is_pro', 'storage_limit_bytes', 'storage_used_bytes', 'lemon_squeezy_customer_id', 'lemon_squeezy_subscription_id')

@admin.register(BillingAuditLog)
class BillingAuditLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'old_state', 'new_state', 'timestamp')
    search_fields = ('user__email', 'webhook_event_id')
    # Prevent anyone from editing the immutable ledger in the GUI
    readonly_fields = [f.name for f in BillingAuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(ProcessedWebhook)
class ProcessedWebhookAdmin(admin.ModelAdmin):
    list_display = ('event_id', 'processed_at')
    readonly_fields = ('event_id', 'processed_at')

@admin.register(DeadLetterQueue)
class DeadLetterQueueAdmin(admin.ModelAdmin):
    list_display = ('event_id', 'error_message', 'created_at')
    readonly_fields = ('event_id', 'payload', 'error_message', 'created_at')
