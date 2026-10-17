"""Admin API routes. Mounted at /api/admin/ by core.urls.

Hyphenated prefixes (`upload-batches`, `usage-logs`) are the contract's
spelling and are load-bearing: the SPA is written against these paths.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from adminapi import views

app_name = "adminapi"

class AdminRouter(DefaultRouter):
    """Staff-gates the router's own index and drops the `.json` suffix
    routes -- the SPA speaks JSON only, and every extra route is one more
    surface the staff check has to cover."""

    APIRootView = views.StaffAPIRootView
    include_format_suffixes = False


router = AdminRouter()
router.register("users", views.AdminUserViewSet, basename="user")
router.register("plans", views.AdminPlanViewSet, basename="plan")
router.register("subscriptions", views.AdminSubscriptionViewSet, basename="subscription")
router.register("proxies", views.AdminProxyViewSet, basename="proxy")
router.register("upload-batches", views.AdminUploadBatchViewSet, basename="uploadbatch")
router.register("content", views.AdminContentViewSet, basename="content")
router.register("usage-logs", views.AdminUsageLogViewSet, basename="usagelog")
router.register("chains", views.AdminChainViewSet, basename="chain")
# Hyphenated like the other multi-word prefixes above.
router.register("audit-log", views.AdminAuditLogViewSet, basename="auditlog")
# Cached IP-to-ASN lookups, plus the candidate-evaluation and refresh
# actions hanging off the same prefix (R2).
router.register("intel", views.AdminIntelViewSet, basename="intel")

urlpatterns = [
    path("auth/login/", views.AdminLoginView.as_view(), name="auth-login"),
    path("auth/refresh/", views.AdminRefreshView.as_view(), name="auth-refresh"),
    path("auth/logout/", views.AdminLogoutView.as_view(), name="auth-logout"),
    path("auth/me/", views.AdminMeView.as_view(), name="auth-me"),
    path("stats/", views.AdminStatsView.as_view(), name="stats"),
    path("", include(router.urls)),
]
