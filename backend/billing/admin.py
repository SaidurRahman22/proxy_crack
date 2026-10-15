"""Stock Django admin for billing (decision D7 -- deliberately minimal)."""

from django.contrib import admin

from billing.models import Plan, Subscription, UsageLog


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "price",
        "duration_days",
        "max_chain_length",
        "max_proxy_tier",
        "is_free_tier",
        "is_active",
    )
    list_filter = ("is_free_tier", "is_active", "max_proxy_tier")
    search_fields = ("code", "name", "play_product_id")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "status", "started_at", "expires_at", "platform")
    list_filter = ("status", "platform", "plan", "auto_renewing")
    search_fields = ("user__email", "platform_txn_id")
    autocomplete_fields = ("user", "plan")
    date_hierarchy = "started_at"


@admin.register(UsageLog)
class UsageLogAdmin(admin.ModelAdmin):
    list_display = ("user", "bytes_up", "bytes_down", "session_start", "session_end")
    list_filter = ("session_start",)
    search_fields = ("user__email",)
    autocomplete_fields = ("user",)
    date_hierarchy = "session_start"
