"""DRF views for the Phase 1 API surface (architecture.md section 3).

    POST /api/auth/register/   /api/auth/login/   /api/auth/refresh/
    GET  /api/proxies/         GET /api/proxies/{id}/   (credentials, throttled)
    GET  /api/chains/          POST /api/chains/        (+ detail CRUD)
    GET  /api/subscription/
    GET  /api/content/announcements/
    POST /api/usage/

Entitlement scoping happens in `get_queryset`, before any client-supplied
filter is applied, so a query parameter can only narrow the result set.
"""

import logging

from django.db.models import Prefetch
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import CreateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from api.filters import ProxyFilter
from api.permissions import IsOwner
from api.serializers import (
    ContentSerializer,
    EmailTokenObtainPairSerializer,
    PlanSerializer,
    ProxyChainSerializer,
    ProxyDetailSerializer,
    ProxyListSerializer,
    RegisterSerializer,
    SubscriptionSerializer,
    UsageLogSerializer,
)
from billing.entitlements import (
    entitlement_payload,
    get_active_subscription,
    get_free_plan,
    resolve_entitlement,
)
from billing.models import Plan
from core.models import Content, ContentKind
from proxies.models import ChainHop, Proxy, ProxyChain

logger = logging.getLogger("proxycrack.api")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class RegisterView(CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = (AllowAny,)
    throttle_scope = "register"


class LoginView(TokenObtainPairView):
    serializer_class = EmailTokenObtainPairSerializer
    permission_classes = (AllowAny,)
    throttle_scope = "login"


class RefreshView(TokenRefreshView):
    permission_classes = (AllowAny,)
    throttle_scope = "login"


# ---------------------------------------------------------------------------
# Proxies
# ---------------------------------------------------------------------------


class ProxyViewSet(viewsets.ReadOnlyModelViewSet):
    """Entitlement-scoped, paginated, rate-limited proxy inventory.

    The list serializer omits credentials entirely. Credentials are available
    only one proxy at a time from the retrieve action, which runs under the
    much tighter `proxy_credentials` throttle and is audit-logged.
    """

    permission_classes = (IsAuthenticated,)
    filterset_class = ProxyFilter
    search_fields = ("country", "city")
    ordering_fields = ("latency_ms", "country", "city", "last_checked")
    ordering = ("latency_ms",)

    def get_throttles(self):
        self.throttle_scope = (
            "proxy_credentials" if self.action == "retrieve" else "proxy_list"
        )
        return super().get_throttles()

    def get_serializer_class(self):
        if self.action == "retrieve":
            return ProxyDetailSerializer
        return ProxyListSerializer

    def get_queryset(self):
        """Scope first, filter second. The entitlement predicate is applied
        here and nowhere else can widen it."""
        entitlement = resolve_entitlement(self.request.user)
        return Proxy.objects.filter(
            is_enabled=True,
            tier__in=entitlement.allowed_proxy_tiers,
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        logger.info(
            "credential_access user=%s proxy=%s tier=%s",
            request.user.pk,
            instance.pk,
            instance.tier,
        )
        return Response(self.get_serializer(instance).data)


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------


class ProxyChainViewSet(viewsets.ModelViewSet):
    """A user's saved chains. Chain length and proxy tier are re-validated on
    every write by `ProxyChainSerializer.validate_proxy_ids`."""

    serializer_class = ProxyChainSerializer
    permission_classes = (IsAuthenticated, IsOwner)

    def get_throttles(self):
        if self.request.method not in ("GET", "HEAD", "OPTIONS"):
            self.throttle_scope = "chain_write"
        else:
            self.throttle_scope = None
        return super().get_throttles()

    def get_queryset(self):
        return (
            ProxyChain.objects.filter(user=self.request.user)
            .prefetch_related(
                Prefetch(
                    "hops",
                    queryset=ChainHop.objects.select_related("proxy").order_by("position"),
                )
            )
            .distinct()
        )

    def perform_destroy(self, instance):
        instance.hops.all().delete()
        instance.delete()


# ---------------------------------------------------------------------------
# Subscription / entitlement
# ---------------------------------------------------------------------------


class SubscriptionView(APIView):
    """GET /api/subscription/ -- current plan plus the limits the server will
    actually enforce. The client renders these; it does not apply them."""

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        entitlement = resolve_entitlement(request.user)
        subscription = get_active_subscription(request.user)

        plan = subscription.plan if subscription else get_free_plan()

        return Response(
            {
                "plan": PlanSerializer(plan).data if plan else None,
                "subscription": (
                    SubscriptionSerializer(subscription).data if subscription else None
                ),
                "entitlements": entitlement_payload(request.user, entitlement),
                "available_plans": PlanSerializer(
                    Plan.objects.filter(is_active=True), many=True
                ).data,
            }
        )


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


class ContentViewSet(viewsets.ReadOnlyModelViewSet):
    """Published operator copy. Readable without a token because onboarding
    text is needed before a user has one; unpublished rows are never exposed."""

    serializer_class = ContentSerializer
    permission_classes = (AllowAny,)
    throttle_scope = "content"
    lookup_field = "key"
    filterset_fields = ("kind",)
    queryset = Content.objects.filter(is_published=True)

    @action(detail=False, methods=["get"], url_path="announcements")
    def announcements(self, request):
        queryset = self.filter_queryset(
            self.get_queryset().filter(kind=ContentKind.ANNOUNCEMENT)
        )
        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response(self.get_serializer(page, many=True).data)
        return Response(self.get_serializer(queryset, many=True).data)


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


class UsageViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """POST /api/usage/ -- the client reports a finished session.

    The row is attributed to the token's user; a `user` field in the body is
    ignored. Reported byte counts are untrusted input and are only ever used
    for accounting, never to grant access.
    """

    serializer_class = UsageLogSerializer
    permission_classes = (IsAuthenticated,)
    throttle_scope = "usage_report"

    def get_queryset(self):
        return self.request.user.usage_logs.all()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        logger.info(
            "usage_reported user=%s up=%s down=%s",
            request.user.pk,
            instance.bytes_up,
            instance.bytes_down,
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)
