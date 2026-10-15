"""API routes. Mounted at /api/ by core.urls."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from api import views

app_name = "api"

router = DefaultRouter()
router.register("proxies", views.ProxyViewSet, basename="proxy")
router.register("chains", views.ProxyChainViewSet, basename="chain")
router.register("content", views.ContentViewSet, basename="content")
router.register("usage", views.UsageViewSet, basename="usage")

urlpatterns = [
    path("auth/register/", views.RegisterView.as_view(), name="auth-register"),
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/refresh/", views.RefreshView.as_view(), name="auth-refresh"),
    path("subscription/", views.SubscriptionView.as_view(), name="subscription"),
    path("", include(router.urls)),
]
