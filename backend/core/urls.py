"""Root URL configuration."""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Proxy Crack operations"
admin.site.site_title = "Proxy Crack"
admin.site.index_title = "Operations"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("api.urls")),
]

if settings.DEBUG:
    # Session login for the DRF browsable API during development only.
    urlpatterns += [path("api-auth/", include("rest_framework.urls"))]
