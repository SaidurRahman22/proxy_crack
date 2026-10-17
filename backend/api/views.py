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
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import CreateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

# The audit seam lives in `adminapi` because that is where it was first
# needed, not because it is admin-only. Importing it here does not couple
# the mobile API to the admin one: `adminapi.audit` depends on nothing but
# the audit model and the logging config.
from adminapi.audit import audit
from api.exceptions import (
    ChainLengthExceeded,
    ChainUnresolvable,
    ProxyNotEntitled,
)
from api.filters import ProxyFilter
from api.permissions import IsOwner
from api.serializers import (
    ChainDiversityRequestSerializer,
    ContentSerializer,
    EmailTokenObtainPairSerializer,
    PlanSerializer,
    ProxyChainSerializer,
    ProxyDetailSerializer,
    ProxyListSerializer,
    RegisterSerializer,
    ResolvedHopSerializer,
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
from proxies.diversity import (
    analyse_chain,
    analyse_proxies,
    pseudonymise_providers,
)
from proxies.models import ChainHop, Proxy, ProxyChain
from proxies.resolution import ChainResolutionError, resolve_chain, resolve_hops

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
    ordering_fields = (
        "latency_ms",
        "latency_p50_ms",
        "latency_p95_ms",
        "success_rate",
        "quality_score",
        "country",
        "city",
        "last_checked",
    )
    # Best-first by default, not fastest-first. `quality_score` folds
    # reliability and latency together with reliability dominant, so the
    # top of the list is the proxy most likely to carry a connection --
    # which is not the same list as "lowest ping", and that difference is
    # the entire point of the scoring model.
    ordering = ("-quality_score",)

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
        """The mobile credential disclosure, and the busiest one there is.

        D18 sends a client here once per hop on every connect and every
        failover, so this endpoint -- not the admin console -- is where
        most paid credentials actually leave the server. It was recording
        a log line and nothing else, which meant the durable trail (P9)
        covered the rare disclosure and missed the common one.

        The log line below is kept byte-for-byte: existing tooling greps
        it and the Phase 1 format is a contract of its own. The audit
        call is purely additive -- no request, response, status code or
        throttle changes -- so `/api/` stays frozen in every sense a
        client can observe.
        """
        instance = self.get_object()
        logger.info(
            "credential_access user=%s proxy=%s tier=%s",
            request.user.pk,
            instance.pk,
            instance.tier,
        )
        # Audit the access, never the value: proxy, actor and IP, with no
        # credential anywhere in the payload.
        audit(
            "credential_access",
            request=request,
            proxy_id=instance.pk,
            tier=instance.tier,
            host=instance.host,
            via="mobile_retrieve",
        )
        return Response(self.get_serializer(instance).data)


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------


class ProxyChainViewSet(viewsets.ModelViewSet):
    """A user's saved chains. Chain length and proxy tier are re-validated on
    every write by `ProxyChainSerializer`."""

    serializer_class = ProxyChainSerializer
    permission_classes = (IsAuthenticated, IsOwner)

    def get_throttles(self):
        action = getattr(self, "action", None)
        if action == "resolve":
            self.throttle_scope = "chain_resolve"
        elif action in ("diversity", "diversity_preview"):
            self.throttle_scope = "chain_diversity"
        elif self.request.method not in ("GET", "HEAD", "OPTIONS"):
            self.throttle_scope = "chain_write"
        else:
            self.throttle_scope = None
        return super().get_throttles()

    def get_serializer_class(self):
        if getattr(self, "action", None) in ("diversity_preview", "resolve_preview"):
            return ChainDiversityRequestSerializer
        return super().get_serializer_class()

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

    @action(detail=True, methods=["post"], url_path="resolve")
    def resolve(self, request, pk=None):
        """`POST /api/chains/{id}/resolve/` -- concrete proxies, right now.

        This is the call the client makes immediately before building the
        engine config, and again when a hop dies mid-session. A fixed hop
        comes back as itself; a constrained hop comes back as whichever
        healthy, entitled proxy currently scores highest for that slot,
        with no proxy used twice in the chain.

        POST rather than GET because the answer is a point-in-time
        decision, not a representation of the chain: two calls a minute
        apart can legitimately return different proxies, and anything that
        caches this as a GET would pin a chain to a dead hop.

        Entitlement is re-resolved here rather than trusted from write
        time. A chain saved under a subscription that has since expired
        must stop resolving, and this is the only place that can notice.

        No credentials in the response -- see `ResolvedHopSerializer`.
        """
        chain = self.get_object()

        try:
            resolution = resolve_chain(chain, user=request.user)
        except ChainResolutionError as exc:
            logger.info(
                "chain_resolution_failed user=%s chain=%s code=%s position=%s",
                request.user.pk,
                chain.pk,
                exc.code,
                exc.position,
            )
            raise _as_api_exception(exc)

        return Response(
            {
                "chain": {"id": chain.pk, "name": chain.name},
                "resolved_at": timezone.now(),
                "hops": ResolvedHopSerializer(
                    resolution.hops,
                    many=True,
                    context=self.get_serializer_context(),
                ).data,
            }
        )


    @action(
        detail=True,
        methods=["get"],
        url_path="diversity",
        url_name="diversity",
    )
    def diversity(self, request, pk=None):
        """`GET /api/chains/{id}/diversity/` -- is this actually a chain?

        The warning the chain builder shows before a user connects. A
        3-hop chain whose hops share an ASN, a datacentre or a vendor
        gives almost none of the protection the user thinks they bought,
        and nothing else in the product would tell them.

        **Resolved first.** A constrained hop has no proxy until it is
        resolved, and the proxy that fills it is what will actually carry
        traffic -- so the analysis is run against the resolution, not
        against the saved definition. When resolution fails the request
        still succeeds: the fixed hops are analysed, the unresolved
        positions are reported as unknown, and `resolution.error` carries
        the reason. A 409 here would be the wrong answer to "warn me about
        this chain", and `POST /resolve/` is where a client finds out that
        it cannot connect.

        **GET, unlike `/resolve/`.** This reads cached intel and never
        performs a lookup, so it is a representation rather than a
        point-in-time decision, and a client must not be able to make the
        server emit DNS by opening a screen.

        **Vendor names are pseudonymised** -- "Vendor A", "Vendor B". The
        user needs to know two hops came from one vendor; publishing
        *which* vendor to every subscriber would hand out the operator's
        supply chain, which P4 makes a live commercial risk.
        """
        chain = self.get_object()

        resolved_proxies = None
        resolution_error = None
        try:
            resolution = resolve_chain(chain, user=request.user)
            resolved_proxies = [hop.proxy for hop in resolution.hops]
        except ChainResolutionError as exc:
            resolution_error = exc.as_detail()

        report = analyse_chain(
            chain,
            resolved_proxies=resolved_proxies,
            transform=pseudonymise_providers,
        )
        payload = report.as_dict()
        payload["chain"] = {"id": chain.pk, "name": chain.name}
        payload["analysed_at"] = timezone.now()
        payload["resolution"] = {
            "resolved": resolved_proxies is not None,
            "error": resolution_error,
        }
        return Response(payload)

    @action(
        detail=False,
        methods=["post"],
        url_path="resolve",
        url_name="resolve-preview",
    )
    def resolve_preview(self, request):
        """`POST /api/chains/resolve/` -- resolve a chain without saving it.

        Connecting should not require persisting anything. Without this,
        "just connect with these proxies" forces a `POST /api/chains/`
        first: it spends the chain-write budget, and it leaves a saved
        preset the user never asked for. Worse, a client that reuses one
        name hits the per-user unique-name constraint on the *second*
        connect and cannot connect again at all -- which is exactly the
        failure that prompted this endpoint.

        Mirrors `diversity_preview`: the chain is in the body, nothing is
        written, and `resolve_hops` is the same resolver the saved path
        uses, so a preview can never drift from the real thing.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        proxy_ids = serializer.validated_data["proxy_ids"]

        entitlement = resolve_entitlement(request.user)
        # Same entitlement gate as the write path, so an id the user may
        # not see fails here with the identical error code.
        proxies = ProxyChainSerializer._check_proxies(proxy_ids, entitlement)

        # Unsaved hops. `resolve_hops` takes hops rather than a chain
        # precisely so this path exists; nothing here touches the DB.
        hops = [
            ChainHop(position=index, proxy=proxies[pk])
            for index, pk in enumerate(proxy_ids)
        ]

        try:
            resolution = resolve_hops(
                hops, user=request.user, entitlement=entitlement
            )
        except ChainResolutionError as exc:
            logger.info(
                "chain_resolution_failed user=%s chain=adhoc code=%s position=%s",
                request.user.pk,
                exc.code,
                exc.position,
            )
            raise _as_api_exception(exc)

        return Response(
            {
                "chain": None,  # nothing was saved
                "resolved_at": timezone.now(),
                "hops": ResolvedHopSerializer(
                    resolution.hops,
                    many=True,
                    context=self.get_serializer_context(),
                ).data,
            }
        )

    @action(
        detail=False,
        methods=["post"],
        url_path="diversity",
        url_name="diversity-preview",
    )
    def diversity_preview(self, request):
        """`POST /api/chains/diversity/` -- score a chain before saving it.

        Same analysis, over an ad-hoc ordered list of proxy ids, so the
        builder can warn while the user is still assembling the chain
        rather than after they have committed to it.

        POST because the chain is in the body; it still performs no
        lookups and writes nothing.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        proxy_ids = serializer.validated_data["proxy_ids"]

        entitlement = resolve_entitlement(request.user)
        # Reuses the chain serializer's entitlement gate rather than
        # repeating it: an id the user may not see must fail here exactly
        # as it fails on write, with the same error code.
        proxies = ProxyChainSerializer._check_proxies(proxy_ids, entitlement)

        ordered = [proxies[pk] for pk in proxy_ids]
        report = analyse_proxies(ordered, transform=pseudonymise_providers)
        payload = report.as_dict()
        payload["analysed_at"] = timezone.now()
        return Response(payload)


def _as_api_exception(exc):
    """Map a resolver failure onto the existing error envelope.

    Entitlement failures reuse the Phase 1 exceptions so that "your plan
    does not cover this" is one `error.code` on the write path and the
    resolve path alike -- a client that already handles
    `proxy_not_entitled` from `POST /api/chains/` needs no new branch.
    Everything else is a 409: the request is fine, the inventory is not.
    """
    if exc.code == "chain_length_exceeded":
        return ChainLengthExceeded(detail=exc.as_detail())
    if exc.code == "hop_proxy_not_entitled":
        return ProxyNotEntitled(detail=exc.as_detail())
    return ChainUnresolvable(detail=exc.as_detail())


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
