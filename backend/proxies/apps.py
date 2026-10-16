from django.apps import AppConfig


class ProxiesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'proxies'

    def ready(self):
        # Registers the probe-retention and scoring-threshold checks.
        from proxies import checks  # noqa: F401
