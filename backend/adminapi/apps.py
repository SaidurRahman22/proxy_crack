from django.apps import AppConfig


class AdminApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "adminapi"
    verbose_name = "Admin API"

    def ready(self):
        # Registers the check that refuses to start if any route under
        # /api/admin/ is reachable without IsStaffUser.
        from adminapi import checks  # noqa: F401
