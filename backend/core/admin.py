"""Stock Django admin for core models (decision D7 -- deliberately minimal)."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from core.models import Content, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    # Rebuilt only because the username field was removed; no extra UX.
    ordering = ("-date_joined",)
    list_display = ("email", "first_name", "last_name", "is_active", "is_staff", "date_joined")
    list_filter = ("is_active", "is_staff", "is_superuser", "date_joined")
    search_fields = ("email", "first_name", "last_name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name")}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2", "is_active", "is_staff"),
            },
        ),
    )


@admin.register(Content)
class ContentAdmin(admin.ModelAdmin):
    list_display = ("key", "kind", "title", "is_published", "priority", "updated_at")
    list_filter = ("kind", "is_published")
    search_fields = ("key", "title", "body")
    prepopulated_fields = {"key": ("title",)}
