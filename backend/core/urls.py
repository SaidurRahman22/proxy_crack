"""Root URL configuration."""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Proxy Crack operations"
admin.site.site_title = "Proxy Crack"
admin.site.index_title = "Operations"

urlpatterns = [
    # Django Admin stays mounted as the superuser fallback (contract §4).
    path("admin/", admin.site.urls),
    # Staff-only admin API. Listed before `api/` for readability only --
    # Django matches `api/admin/` against this entry first either way,
    # because `api.urls` has no `admin/` route to shadow it.
    path("api/admin/", include("adminapi.urls")),
    path("api/", include("api.urls")),
]

if settings.DEBUG:
    # Session login for the DRF browsable API during development only.
    urlpatterns += [path("api-auth/", include("rest_framework.urls"))]
