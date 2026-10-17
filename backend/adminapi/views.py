"""Staff-only admin API -- the backend half of `Project/admin-api-contract.md`.

Mounted at `/api/admin/`. Additive: nothing here touches `/api/`, which the
mobile app depends on and which is entitlement-scoped by design.

Every view in this module is staff-gated. The gating is expressed once, in
`AdminViewMixin`, and enforced a second time at startup by
`adminapi.checks.check_admin_endpoints_require_staff`, which walks the
URLconf and refuses to let the process boot if any route under
`/api/admin/` is reachable without `IsStaffUser`. The two auth endpoints
that cannot require a token -- login and refresh -- are named explicitly in
that check's exemption list, so an accidentally-open endpoint is a startup
error rather than a silent data leak.
"""

import logging
import os
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Prefetch, Q, Sum
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.renderers import BrowsableAPIRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.routers import APIRootView
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from adminapi.audit import (
    PLAN_ENTITLEMENT_FIELDS,
    USER_PRIVILEGE_FIELDS,
    audit,
    changed_fields,
)
from adminapi.auth import (
    AdminLoginFailed,
    AdminTokenObtainPairSerializer,
    AdminTokenRefreshSerializer,
    LogoutSerializer,
)
from adminapi.authentication import AdminJWTAuthentication
from adminapi.exceptions import (
    CannotDeleteSelf,
    Conflict,
    PlanHasActiveSubscriptions,
    PlanInUse,
    ProxyInUse,
    SetPasswordDenied,
)
from adminapi.throttling import (
    AdminBulkThrottle,
    AdminIntelThrottle,
    AdminCredentialDailyThrottle,
    AdminCredentialHourlyThrottle,
    AdminCSVUploadThrottle,
    AdminLoginEmailThrottle,
    AdminLoginIPThrottle,
    AdminProbeThrottle,
    AdminProviderSyncThrottle,
)
from adminapi.filters import (
    MAX_IDS,
    AdminAuditLogFilter,
    AdminChainFilter,
    AdminContentFilter,
    AdminIpIntelFilter,
    AdminPlanFilter,
    AdminProviderSyncRunFilter,
    AdminProxyFilter,
    AdminSubscriptionFilter,
    AdminUploadBatchFilter,
    AdminUsageLogFilter,
    AdminUserFilter,
    IdsFilterBackend,
)
from adminapi.pagination import AdminPagination
from adminapi.permissions import IsStaffUser
from adminapi.models import AdminAuditLog
from adminapi.serializers import (
    AdminAuditLogSerializer,
    AdminChainSerializer,
    AdminContentSerializer,
    AdminIpIntelRecordSerializer,
    AdminMeSerializer,
    AdminPlanSerializer,
    AdminProxyDetailSerializer,
    AdminProxyListSerializer,
    AdminSubscriptionSerializer,
    AdminProviderSyncRunSerializer,
    AdminUploadBatchSerializer,
    AdminUsageLogSerializer,
    AdminUserSerializer,
    BulkDeleteSerializer,
    BulkEnableSerializer,
    IntelCandidateAnalysisSerializer,
    IntelRefreshSerializer,
    ProbeRequestSerializer,
    ProviderSyncRequestSerializer,
    ProxyCSVUploadSerializer,
    SetPasswordSerializer,
    parse_batch_errors,
)
from billing.models import (
    Plan,
    Subscription,
    SubscriptionStatus,
    UsageLog,
)
from core.models import Content
from proxies.diversity import (
    HopInput,
    analyse_chain,
    analyse_hops,
)
from proxies.health import record_probe_results
from proxies.importers import CSVImportError, import_proxies_from_csv
from proxies.intel import (
    cached_facts,
    get_ip_intel,
    lookup_hosts,
    normalise_host,
)
from proxies.providers import (
    ProviderCredentialsMissing,
    ProviderSyncError,
    SyncAction,
    get_provider_sync,
    sync_provider,
)
from proxies.probing import (
    DEFAULT_CONCURRENCY,
    DEFAULT_TIMEOUT,
    ProbeUnsupported,
    get_prober,
    probe_batch,
    targets_for,
)
from proxies.models import (
    ChainHop,
    IpIntelRecord,
    ProviderSyncRun,
    Proxy,
    ProxyChain,
    ProxyProtocol,
    ProxyTier,
    UploadBatch,
)

logger = logging.getLogger("proxycrack.adminapi")

User = get_user_model()

STATS_WINDOW_DAYS = 7

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Uploads are read into memory twice by the importer, so the ceiling is a
# memory control, not a politeness one.
CSV_EXTENSIONS = (".csv", ".txt")


def revoke_refresh_tokens(user):
    """Blacklist every outstanding refresh token for `user`.

    Returns the number newly blacklisted. Used after a password reset: a
    reset that leaves the old sessions renewable is not a reset.
    """
    revoked = 0
    for token in OutstandingToken.objects.filter(user=user):
        _, created = BlacklistedToken.objects.get_or_create(token=token)
        revoked += int(created)
    return revoked


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------


class AdminViewMixin:
    """The one place the admin API's access rules are declared.

    Nothing below sets `permission_classes` itself; everything inherits it.
    A new viewset that forgets to inherit this mixin is caught at startup by
    `adminapi.checks`, not in production.
    """

    # Control 1, at the authentication layer: the token must have been
    # minted by /api/admin/auth/login/. A mobile token is refused here
    # before any permission class runs -- see adminapi.authentication.
    authentication_classes = (AdminJWTAuthentication,)
    # Control 2, at the authorisation layer.
    permission_classes = (IsAuthenticated, IsStaffUser)
    # JSON only. The default renderer list includes the browsable API, and
    # a registered renderer is a second serialisation path that nobody
    # reviews -- `?format=` must not become a way to shape a response.
    renderer_classes = (JSONRenderer,) + (
        (BrowsableAPIRenderer,) if settings.DEBUG else ()
    )
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "admin"

    def get_throttles(self):
        """Reads and writes are not the same risk, so they are not the same
        budget. Subclasses narrow this further per action."""
        if getattr(self.request, "method", "GET") in WRITE_METHODS:
            self.throttle_scope = "admin_write"
        else:
            self.throttle_scope = "admin"
        return super().get_throttles()


class AdminModelViewSet(AdminViewMixin, viewsets.ModelViewSet):
    pagination_class = AdminPagination
    filter_backends = (IdsFilterBackend, DjangoFilterBackend, SearchFilter, OrderingFilter)


class AdminReadOnlyViewSet(AdminViewMixin, viewsets.ReadOnlyModelViewSet):
    pagination_class = AdminPagination
    filter_backends = (IdsFilterBackend, DjangoFilterBackend, SearchFilter, OrderingFilter)


class StaffAPIRootView(AdminViewMixin, APIRootView):
    """The router's index at `/api/admin/`.

    DRF's stock `APIRootView` inherits the project default of
    `IsAuthenticated`, which would make the admin route map readable by any
    mobile user. It is a list of URLs rather than data, but "every admin
    endpoint is staff-only" is the rule, and an exception here would be the
    one route the startup check has to be told to ignore.
    """



# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class AdminLoginView(TokenObtainPairView):
    """`POST /api/admin/auth/login/`.

    AllowAny by necessity -- there is no token yet. The staff check lives in
    the serializer and runs before a token is minted. Exempted by name in
    `adminapi.checks`.
    """

    serializer_class = AdminTokenObtainPairSerializer
    permission_classes = (AllowAny,)
    authentication_classes = ()
    renderer_classes = (JSONRenderer,)
    # Per IP and per submitted email. One key alone protects the wrong
    # thing: per-IP lets a botnet spread the guessing out, per-email lets
    # one attacker lock every operator out of the console.
    throttle_classes = (AdminLoginIPThrottle, AdminLoginEmailThrottle)

    def post(self, request, *args, **kwargs):
        try:
            return super().post(request, *args, **kwargs)
        except AdminLoginFailed:
            # Only failures consume the per-email budget, so an operator
            # signing in through a working day never exhausts it while a
            # guesser exhausts it immediately.
            AdminLoginEmailThrottle().record_failure(request, self)
            raise


class AdminRefreshView(TokenRefreshView):
    """`POST /api/admin/auth/refresh/`. AllowAny for the same reason as
    login: the access token being renewed is, by definition, expired. The
    refresh token's subject is re-checked for staff in the serializer."""

    serializer_class = AdminTokenRefreshSerializer
    permission_classes = (AllowAny,)
    authentication_classes = ()
    renderer_classes = (JSONRenderer,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "admin_refresh"


class AdminLogoutView(AdminViewMixin, GenericAPIView):
    """`POST /api/admin/auth/logout/` {refresh} -> 204.

    Blacklists the refresh token. Returns 204 even when the token is already
    expired or blacklisted: logout is the SPA's escape hatch and must never
    fail in a way that strands a session.

    **Audited (P24).** This wrote a log line and no audit row, while the
    mobile logout built later wrote both. That asymmetry was a P9
    regression on the console surface: P9 made audit *rows* the evidence --
    the copy that survives a log handler nobody configured -- so admin
    sign-out was the one authentication event on this API leaving none.
    Login success and login failure were both already rows; the end of the
    session was the missing third. It now goes through the same `audit()`
    seam, which is also what gives it the IP, the user agent and the
    durable write for free.

    `outcome` is recorded because the two cases are operationally
    different and indistinguishable afterwards otherwise: `blacklisted`
    means a live session really was ended here, while `token_unusable`
    means the SPA sent something already dead and the row is a record of
    intent rather than of effect. Asking "when did this staff account last
    actually end a session" has to be answerable after a laptop goes
    missing.

    `via="admin"` mirrors the mobile endpoint's `via="mobile"` on the same
    `logout` action, so one query answers the question across both
    surfaces and a filter on `via` splits them.
    """

    serializer_class = LogoutSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data["refresh"]).blacklist()
        except TokenError as exc:
            outcome = "token_unusable"
            logger.info("admin_logout_token_unusable user=%s (%s)", request.user.pk, exc)
        else:
            outcome = "blacklisted"
            logger.info("admin_logout user=%s", request.user.pk)

        # After the blacklist attempt, never before: an audit row claiming
        # a session ended is only worth writing once the attempt to end it
        # has resolved one way or the other. `audit()` is explicitly built
        # not to fail the request it is recording, so there is no risk in
        # the ordering.
        audit(
            "admin_logout",
            request=request,
            target_user_id=request.user.pk,
            outcome=outcome,
            via="admin",
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminMeView(AdminViewMixin, APIView):
    """`GET /api/admin/auth/me/`."""

    def get(self, request):
        return Response(AdminMeSerializer(request.user).data)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class AdminStatsView(AdminViewMixin, APIView):
    """`GET /api/admin/stats/` -- the dashboard, in five aggregate queries.

    Subscription buckets, spelled out because the words are ambiguous:
      active    status=active AND (no expiry OR expiry in the future)
      expired   status=expired, OR status=active but already past expiry
                (a row nothing has swept yet is expired in fact)
      free_tier / paid   active subscriptions split on plan.is_free_tier
    Cancelled rows are in none of them.
    """

    def get(self, request):
        now = timezone.now()
        cutoff = now - timedelta(days=STATS_WINDOW_DAYS)

        users = User.objects.aggregate(
            total=Count("pk"),
            staff=Count("pk", filter=Q(is_staff=True)),
            new_7d=Count("pk", filter=Q(date_joined__gte=cutoff)),
        )

        live = Q(status=SubscriptionStatus.ACTIVE) & (
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        )
        stale = Q(status=SubscriptionStatus.EXPIRED) | (
            Q(status=SubscriptionStatus.ACTIVE) & Q(expires_at__lte=now)
        )
        subscriptions = Subscription.objects.aggregate(
            active=Count("pk", filter=live),
            expired=Count("pk", filter=stale),
            free_tier=Count("pk", filter=live & Q(plan__is_free_tier=True)),
            paid=Count("pk", filter=live & Q(plan__is_free_tier=False)),
        )

        proxies = Proxy.objects.aggregate(
            total=Count("pk"),
            alive=Count("pk", filter=Q(is_alive=True)),
            dead=Count("pk", filter=Q(is_alive=False)),
            free=Count("pk", filter=Q(tier=ProxyTier.FREE)),
            premium=Count("pk", filter=Q(tier=ProxyTier.PREMIUM)),
            **{
                f"protocol_{value}": Count("pk", filter=Q(protocol=value))
                for value in ProxyProtocol.values
            },
        )
        by_protocol = {
            value: proxies.pop(f"protocol_{value}") for value in ProxyProtocol.values
        }

        usage = UsageLog.objects.filter(session_start__gte=cutoff).aggregate(
            bytes_up_7d=Sum("bytes_up"),
            bytes_down_7d=Sum("bytes_down"),
            sessions_7d=Count("pk"),
        )

        last_batch = UploadBatch.objects.order_by("-created_at").first()

        return Response(
            {
                "users": users,
                "subscriptions": subscriptions,
                "proxies": {**proxies, "by_protocol": by_protocol},
                "usage": {
                    "bytes_up_7d": usage["bytes_up_7d"] or 0,
                    "bytes_down_7d": usage["bytes_down_7d"] or 0,
                    "sessions_7d": usage["sessions_7d"] or 0,
                },
                "uploads": {
                    "last_batch_at": last_batch.created_at if last_batch else None,
                    "last_batch_rows": last_batch.row_count if last_batch else 0,
                },
            }
        )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class AdminUserViewSet(AdminModelViewSet):
    """Full user CRUD. The password hash is not a serializer field at all,
    so no code path can return it."""

    serializer_class = AdminUserSerializer
    filterset_class = AdminUserFilter
    search_fields = ("email", "first_name", "last_name")
    ordering_fields = ("date_joined", "email", "last_login", "id")
    ordering = ("-date_joined",)

    def get_queryset(self):
        return User.objects.prefetch_related(
            Prefetch(
                "subscriptions",
                queryset=Subscription.objects.filter(
                    status=SubscriptionStatus.ACTIVE
                )
                .select_related("plan")
                .order_by("-started_at"),
                to_attr="active_subscriptions",
            )
        )

    def perform_update(self, serializer):
        """Privilege and activation changes are the events worth keeping."""
        changes = changed_fields(
            serializer.instance, serializer.validated_data, USER_PRIVILEGE_FIELDS
        )
        instance = serializer.save()
        if changes:
            audit(
                "admin_user_privileges_changed",
                request=self.request,
                level=logging.WARNING,
                target_user_id=instance.pk,
                target_email=instance.email,
                changes=changes,
            )

    def perform_create(self, serializer):
        instance = serializer.save()
        audit(
            "admin_user_created",
            request=self.request,
            target_user_id=instance.pk,
            target_email=instance.email,
            is_staff=instance.is_staff,
            is_superuser=instance.is_superuser,
        )

    def perform_destroy(self, instance):
        if instance.pk == self.request.user.pk:
            # Not in the contract, but deleting the account you are holding
            # a token for is never the intent and is not undoable.
            raise CannotDeleteSelf(
                {
                    "message": "You cannot delete the account you are signed in as.",
                    "user_id": instance.pk,
                }
            )
        audit(
            "admin_user_deleted",
            request=self.request,
            level=logging.WARNING,
            target_user_id=instance.pk,
            target_email=instance.email,
        )
        instance.delete()

    def _check_set_password_allowed(self, target):
        """The privilege boundary the contract does not draw.

        Setting another account's password is account takeover, and the
        quietest route to the worst outcome: set the superuser's password,
        then sign in as them. With Django Admin removed there is no
        second, broader console at the end of that path any more -- which
        does not soften the rule, it sharpens it, because this boundary is
        now the only thing between a staff account and a superuser's. So
        resetting a *peer's* password is a superuser action, and resetting
        a superuser's password is a superuser action. Your own is always
        yours.
        """
        actor = self.request.user
        if target.pk == actor.pk:
            return
        if actor.is_superuser:
            return
        if target.is_superuser:
            raise SetPasswordDenied(
                {
                    "message": (
                        "Only a superuser may set a superuser's password."
                    ),
                    "target_user_id": target.pk,
                }
            )
        if target.is_staff:
            raise SetPasswordDenied(
                {
                    "message": (
                        "Only a superuser may set another staff member's "
                        "password."
                    ),
                    "target_user_id": target.pk,
                }
            )

    @action(detail=True, methods=["post"], url_path="set_password")
    def set_password(self, request, pk=None):
        """`POST /api/admin/users/{id}/set_password/` {password} -> 204."""
        user = self.get_object()
        self._check_set_password_allowed(user)

        serializer = SetPasswordSerializer(
            data=request.data,
            context={**self.get_serializer_context(), "target_user": user},
        )
        serializer.is_valid(raise_exception=True)
        user.set_password(serializer.validated_data["password"])
        user.save(update_fields=["password"])

        # A password reset that leaves the old sessions running is not a
        # reset. SimpleJWT's blacklist covers refresh tokens, so this ends
        # the target's ability to renew; their current access token still
        # works until it expires (ACCESS_TOKEN_LIFETIME).
        revoked = revoke_refresh_tokens(user)

        audit(
            "admin_set_password",
            request=request,
            level=logging.WARNING,
            target_user_id=user.pk,
            target_email=user.email,
            self_service=user.pk == request.user.pk,
            refresh_tokens_revoked=revoked,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------


class AdminPlanViewSet(AdminModelViewSet):
    serializer_class = AdminPlanSerializer
    filterset_class = AdminPlanFilter
    search_fields = ("code", "name", "description", "play_product_id")
    ordering_fields = ("sort_order", "price", "code", "id")
    ordering = ("sort_order", "price")

    def get_queryset(self):
        return Plan.objects.annotate(
            active_subscription_count_annotated=Count(
                "subscriptions",
                filter=Q(subscriptions__status=SubscriptionStatus.ACTIVE),
                distinct=True,
            )
        )

    def perform_update(self, serializer):
        """The highest-value audit event in the system.

        A staff account that wants the proxy credentials does not have to
        iterate the detail endpoint at all. One PATCH setting the free
        plan's `max_proxy_tier` to `premium` re-entitles the entire free
        user base, and the credentials are then scraped through the
        **mobile** API with throwaway free accounts -- touching no admin
        throttle, reading no admin detail endpoint, leaving no credential
        audit line. The cross-surface move is invisible to every control
        that guards this API, so the plan write itself is what has to be
        loud.
        """
        instance = serializer.instance
        changes = changed_fields(
            instance, serializer.validated_data, PLAN_ENTITLEMENT_FIELDS
        )
        self._guard_last_free_plan(instance, serializer.validated_data)
        updated = serializer.save()
        if changes:
            audit(
                "admin_plan_entitlement_changed",
                request=self.request,
                level=logging.WARNING,
                plan_id=updated.pk,
                plan_code=updated.code,
                changes=changes,
            )

    def _guard_last_free_plan(self, instance, validated_data):
        """Deactivating the last free plan is not a small edit.

        `resolve_entitlement` falls back to `ENTITLEMENT_FALLBACK` when no
        active free plan exists, so every free user silently drops to the
        hardcoded floor -- a product-wide change with no error anywhere.
        """
        if not instance.is_free_tier:
            return
        going_inactive = validated_data.get("is_active", instance.is_active) is False
        losing_free_flag = validated_data.get("is_free_tier", True) is False
        if not (going_inactive or losing_free_flag):
            return

        siblings = Plan.objects.filter(is_free_tier=True, is_active=True).exclude(
            pk=instance.pk
        )
        if siblings.exists():
            return

        raise Conflict(
            {
                "message": (
                    "This is the only active free plan. Removing it drops "
                    "every free user to the hardcoded entitlement fallback "
                    "with no other signal. Create a replacement free plan "
                    "first."
                ),
                "plan": instance.code,
                "code": "last_free_plan",
            }
        )

    def perform_destroy(self, instance):
        """The guard from the contract: a plan with live subscribers is not
        deletable, and the operator is told how many there are."""
        active = instance.subscriptions.filter(
            status=SubscriptionStatus.ACTIVE
        ).count()
        if active:
            raise PlanHasActiveSubscriptions(
                {
                    "message": (
                        "This plan still has active subscriptions. Move or "
                        "cancel them before deleting it."
                    ),
                    "plan": instance.code,
                    "active_subscriptions": active,
                }
            )
        try:
            instance.delete()
        except ProtectedError:
            # Subscription.plan is PROTECT, so cancelled and expired history
            # blocks the delete too. Without this it would be a 500.
            total = instance.subscriptions.count()
            raise PlanInUse(
                {
                    "message": (
                        "This plan is referenced by subscription history and "
                        "cannot be deleted. Deactivate it instead."
                    ),
                    "plan": instance.code,
                    "active_subscriptions": 0,
                    "subscriptions": total,
                }
            )


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


class AdminSubscriptionViewSet(AdminModelViewSet):
    serializer_class = AdminSubscriptionSerializer
    filterset_class = AdminSubscriptionFilter
    search_fields = ("user__email", "platform_txn_id")
    ordering_fields = ("started_at", "expires_at", "id")
    ordering = ("-started_at",)
    queryset = Subscription.objects.select_related("user", "plan")

    def perform_create(self, serializer):
        instance = serializer.save()
        audit(
            "admin_subscription_created",
            request=self.request,
            level=logging.WARNING,
            subscription_id=instance.pk,
            target_user_id=instance.user_id,
            plan_code=instance.plan.code,
            status=instance.status,
            expires_at=instance.expires_at,
        )

    def perform_update(self, serializer):
        changes = changed_fields(
            serializer.instance,
            serializer.validated_data,
            ("plan", "status", "expires_at"),
        )
        instance = serializer.save()
        if changes:
            audit(
                "admin_subscription_changed",
                request=self.request,
                level=logging.WARNING,
                subscription_id=instance.pk,
                target_user_id=instance.user_id,
                changes=changes,
            )

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """`POST /api/admin/subscriptions/{id}/cancel/` -> 200 + the row."""
        subscription = self.get_object()
        subscription.status = SubscriptionStatus.CANCELLED
        subscription.save(update_fields=["status", "updated_at"])
        audit(
            "admin_subscription_cancelled",
            request=request,
            level=logging.WARNING,
            subscription_id=subscription.pk,
            target_user_id=subscription.user_id,
            plan_code=subscription.plan.code,
        )
        return Response(self.get_serializer(subscription).data)


# ---------------------------------------------------------------------------
# Proxies
# ---------------------------------------------------------------------------


class AdminProxyViewSet(AdminModelViewSet):
    """Full proxy CRUD, bulk operations, and CSV import.

    Credentials are decrypted by `AdminProxyDetailSerializer`, which is used
    only for single-object actions. `list` uses a serializer that has no
    credential fields, so there is no flag, header or query parameter that
    can make the inventory come back with credentials attached.
    """

    filterset_class = AdminProxyFilter
    search_fields = ("host", "country", "city")
    ordering_fields = (
        "latency_ms",
        "latency_p50_ms",
        "latency_p95_ms",
        "success_rate",
        "quality_score",
        "probe_count",
        "consecutive_failures",
        "last_checked",
        "host",
        "country",
        "id",
    )
    ordering = ("country", "city", "host")
    queryset = Proxy.objects.all()

    def get_serializer_class(self):
        if self.action == "list":
            return AdminProxyListSerializer
        if self.action == "retrieve":
            # Credentials are NOT part of an ordinary detail read. Opening a
            # drawer is not a disclosure; POST /reveal/ is.
            return AdminProxyListSerializer
        if self.action == "bulk_enable":
            return BulkEnableSerializer
        if self.action == "bulk_delete":
            return BulkDeleteSerializer
        if self.action == "upload_csv":
            return ProxyCSVUploadSerializer
        if self.action == "probe":
            return ProbeRequestSerializer
        if self.action == "sync":
            return ProviderSyncRequestSerializer
        return AdminProxyDetailSerializer

    def get_throttles(self):
        """Credential disclosure is not an ordinary read and is not budgeted
        like one. The hourly limit bounds a burst; the daily ceiling bounds
        patience, which is what a scraper actually has."""
        action = getattr(self, "action", None)
        if action == "reveal":
            return [AdminCredentialHourlyThrottle(), AdminCredentialDailyThrottle()]
        if action in ("bulk_enable", "bulk_delete"):
            return [AdminBulkThrottle()]
        if action == "upload_csv":
            return [AdminCSVUploadThrottle()]
        if action == "probe":
            return [AdminProbeThrottle()]
        if action == "sync":
            return [AdminProviderSyncThrottle()]
        return super().get_throttles()

    def _audit_credentials(self, instance, reason):
        """Every path that puts a decrypted credential in a response.

        That includes `create` and `update`, whose responses echo the
        credentials back: the log records what left the server, not which
        handler ran. The credential value itself is never recorded.
        """
        audit(
            "credential_access",
            request=self.request,
            proxy_id=instance.pk,
            tier=instance.tier,
            host=instance.host,
            via=reason,
        )
        # Kept in the Phase 1 format as well so existing log tooling and
        # the api/ audit line stay greppable with one pattern.
        logger.info(
            "credential_access user=%s proxy=%s tier=%s via=%s",
            self.request.user.pk,
            instance.pk,
            instance.tier,
            reason,
        )

    @action(detail=True, methods=["post"], url_path="reveal")
    def reveal(self, request, *args, **kwargs):
        """Decrypt and return this proxy's credentials.

        Deliberately separate from `retrieve`. While credentials rode on the
        detail GET, the audit log recorded page views rather than
        disclosures, and a single throttle number had to serve both drawer
        usability and anti-scrape -- usability always wins that argument.
        A POST makes disclosure an explicit act with its own budget
        (120/hour AND 400/day) and its own audit event.
        """
        instance = self.get_object()
        self._audit_credentials(instance, "admin_reveal")
        serializer = AdminProxyDetailSerializer(
            instance, context=self.get_serializer_context()
        )
        return Response(serializer.data)

    def perform_create(self, serializer):
        instance = serializer.save()
        # The create/update response echoes the credentials back, so the
        # same audit line fires -- the log is a record of what left the
        # server, not of which handler ran.
        self._audit_credentials(instance, "admin_create")

    def perform_update(self, serializer):
        instance = serializer.save()
        self._audit_credentials(instance, "admin_update")

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            raise ProxyInUse(
                {
                    "message": (
                        "This proxy is a hop in one or more saved user "
                        "chains. Disable it instead of deleting it."
                    ),
                    "proxy_ids": [instance.pk],
                    "chains": ChainHop.objects.filter(proxy=instance).count(),
                }
            )

    @action(detail=False, methods=["post"], url_path="bulk_enable")
    def bulk_enable(self, request):
        """`{ids[], is_enabled}` -> `{updated: n}`."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data["ids"]
        is_enabled = serializer.validated_data["is_enabled"]
        # `.update()` never touches the credential columns, so the
        # EncryptedCharField converters are not bypassed here. Any future
        # bulk write that does touch them must go through the ORM instance
        # or it writes plaintext.
        updated = Proxy.objects.filter(pk__in=ids).update(
            is_enabled=is_enabled, updated_at=timezone.now()
        )
        audit(
            "admin_bulk_enable",
            request=request,
            level=logging.WARNING,
            is_enabled=is_enabled,
            requested=len(ids),
            updated=updated,
            proxy_ids=ids,
        )
        return Response({"updated": updated})

    @action(detail=False, methods=["post"], url_path="bulk_delete")
    def bulk_delete(self, request):
        """`{ids[]}` -> `{deleted: n}`.

        All or nothing. `ChainHop.proxy` is PROTECT, so a partial delete
        would leave the operator guessing which rows went; the blocked ids
        come back in a 409 instead.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data["ids"]

        blocked = sorted(
            set(
                ChainHop.objects.filter(proxy_id__in=ids).values_list(
                    "proxy_id", flat=True
                )
            )
        )
        if blocked:
            raise ProxyInUse(
                {
                    "message": (
                        "Some of these proxies are hops in saved user chains "
                        "and cannot be deleted. Nothing was deleted."
                    ),
                    "proxy_ids": blocked,
                    "chains": ChainHop.objects.filter(proxy_id__in=blocked).count(),
                }
            )

        deleted, _ = Proxy.objects.filter(pk__in=ids).delete()
        audit(
            "admin_bulk_delete",
            request=request,
            level=logging.WARNING,
            requested=len(ids),
            deleted=deleted,
            proxy_ids=ids,
        )
        return Response({"deleted": deleted})

    @action(detail=False, methods=["post"], url_path="probe")
    def probe(self, request):
        """`{ids[]}` -> probe those proxies now and return what happened.

        The normal path is the scheduled `probe_proxies` management
        command. This endpoint exists for the one case a schedule cannot
        serve: an operator has just fixed something, or has just been told
        a provider is down, and needs an answer in the next five seconds
        rather than at the next tick.

        **Synchronous, and deliberately so.** A queue would be the textbook
        answer, and a broker is an operational dependency -- one more thing
        to deploy, monitor, secure and restore -- bought here for a button
        that is pressed a few times a day. Instead the blast radius is
        capped: `PROXY_PROBE_API_MAX_IDS` ids, a per-probe timeout, bounded
        concurrency, and its own 30/hour throttle. When probe volume
        outgrows that, `probe_proxies` is where the queue goes, and this
        endpoint becomes an enqueue.

        Disabled proxies are probed if asked for by id: the operator may
        be deciding whether to re-enable one, and refusing to measure it
        would be unhelpful. The command's bulk path still skips them.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data["ids"]

        proxies = list(Proxy.objects.filter(pk__in=ids))
        found = {proxy.pk for proxy in proxies}
        missing = sorted(set(ids) - found)

        timeout = float(
            getattr(settings, "PROXY_PROBE_TIMEOUT_SECONDS", DEFAULT_TIMEOUT)
        )
        concurrency = int(
            getattr(settings, "PROXY_PROBE_CONCURRENCY", DEFAULT_CONCURRENCY)
        )

        outcome = probe_batch(
            get_prober(),
            targets_for(proxies),
            timeout=timeout,
            concurrency=concurrency,
        )
        record_probe_results(outcome.results)

        audit(
            "admin_probe_run",
            request=request,
            requested=len(ids),
            probed=len(outcome.results),
            succeeded=outcome.succeeded,
            failed=outcome.failed,
            skipped=len(outcome.skipped),
            proxy_ids=ids,
        )

        # Re-read so the response carries the health the probe just wrote,
        # rather than the stale values loaded before it ran.
        refreshed = {
            proxy.pk: proxy for proxy in Proxy.objects.filter(pk__in=found)
        }
        results = [
            {
                "proxy": proxy_id,
                "succeeded": result.succeeded,
                "latency_ms": result.latency_ms,
                "error_kind": result.error_kind or None,
                "detail": result.detail or None,
                "health": AdminProxyListSerializer(refreshed[proxy_id]).data
                if proxy_id in refreshed
                else None,
            }
            for proxy_id, result, _target in outcome.results
        ]

        return Response(
            {
                "requested": len(ids),
                "probed": len(outcome.results),
                "succeeded": outcome.succeeded,
                "failed": outcome.failed,
                # This prober cannot measure these -- an unsupported
                # protocol, or a Shadowsocks cipher we do not implement
                # (SS2022). They write no probe row and stay "unprobed"
                # rather than being recorded as dead: a gap in our prober
                # is not a verdict on the proxy. `code` separates the two
                # causes so the SPA can say which.
                "skipped": [
                    {
                        "proxy": pk,
                        "reason": reason,
                        "code": outcome.skipped_codes.get(
                            pk, ProbeUnsupported.UNSUPPORTED_PROTOCOL
                        ),
                    }
                    for pk, reason in outcome.skipped
                ],
                # The prober itself raised. Our bug, not the proxy's, so
                # nothing was recorded against the proxy.
                "errored": [
                    {"proxy": pk, "error": error} for pk, error in outcome.errored
                ],
                "not_found": missing,
                "results": results,
            }
        )

    @action(detail=False, methods=["post"], url_path="sync")
    def sync(self, request):
        """`POST /api/admin/proxies/sync/` -- pull the provider's listing now.

        The normal path is the scheduled `sync_webshare` command. This is
        the button for the case a schedule cannot serve: the provider has
        just said they rotated addresses, and the operator needs the
        inventory to agree with reality before the next tick.

        **Defaults to a dry run**, unlike every other action on this
        viewset, and deliberately. Reconciling is the only admin operation
        that can disable a large slice of the sellable inventory on the
        strength of a third party's HTTP response, so the rehearsal is what
        you get by omission. `{"dry_run": false}` commits.

        Synchronous, for the same reason `probe` is: a broker is an
        operational dependency bought for a button pressed a few times a
        week. The blast radius is capped instead --
        `PROVIDER_SYNC_API_MAX_RECORDS`, a per-request timeout, and the
        tightest throttle on the admin API.

        **The API token appears nowhere in this handler's output.** Not in
        the response, not in the audit payload, not in a log line, and not
        in an error: `proxies.providers` constructs every message from
        status codes and endpoint identities, never from a response body
        (which carries the account's proxy passwords in plaintext).
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dry_run = serializer.validated_data["dry_run"]
        write_intel = serializer.validated_data["write_intel"]
        limit = serializer.validated_data.get("limit") or int(
            getattr(settings, "PROVIDER_SYNC_API_MAX_RECORDS", 500)
        )

        adapter = get_provider_sync()
        try:
            outcome = sync_provider(
                adapter,
                limit=limit,
                dry_run=dry_run,
                write_intel=write_intel,
                triggered_by=request.user,
            )
        except ProviderCredentialsMissing as exc:
            # 503, not 500: the service is correctly configured code that
            # has not been given a credential. The message names the
            # setting and never its value.
            audit(
                "admin_provider_sync_failed",
                request=request,
                level=logging.WARNING,
                provider=getattr(adapter, "provider", adapter.name),
                reason="credentials_missing",
            )
            return Response(
                {"detail": str(exc), "code": "provider_credentials_missing"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except ProviderSyncError as exc:
            audit(
                "admin_provider_sync_failed",
                request=request,
                level=logging.WARNING,
                provider=getattr(adapter, "provider", adapter.name),
                reason="provider_unavailable",
                detail=str(exc)[:200],
            )
            return Response(
                {"detail": str(exc), "code": "provider_unavailable"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        audit(
            "admin_provider_sync",
            request=request,
            # WARNING even on success: a run that disables inventory is a
            # line a human should see, and distinguishing "worth reading"
            # from "failed" is what the severity is for here.
            level=(
                logging.WARNING
                if outcome.count(SyncAction.DISABLED) and not dry_run
                else logging.INFO
            ),
            provider=outcome.provider,
            source=outcome.source,
            dry_run=dry_run,
            run_id=outcome.run.pk if outcome.run else None,
            status=outcome.status,
            fetched=len(outcome.listing.records) if outcome.listing else 0,
            complete=bool(outcome.listing and outcome.listing.complete),
            **{action: outcome.count(action) for action in outcome.counts},
            intel_written=outcome.intel_written,
            intel_skipped=outcome.intel_skipped,
        )

        return Response(
            {
                "run": AdminProviderSyncRunSerializer(outcome.run).data
                if outcome.run
                else None,
                "provider": outcome.provider,
                "source": outcome.source,
                "dry_run": dry_run,
                "status": outcome.status,
                # False means the provider's listing was not fully
                # retrieved, and therefore that nothing was disabled. The
                # SPA must say so rather than render a tally of zeroes
                # that reads as "nothing vanished".
                "complete": bool(outcome.listing and outcome.listing.complete),
                "fetched": len(outcome.listing.records) if outcome.listing else 0,
                "pages": outcome.listing.pages if outcome.listing else 0,
                "counts": outcome.counts,
                "intel_written": outcome.intel_written,
                "intel_skipped": outcome.intel_skipped,
                "errors": outcome.errors,
                # Per-endpoint detail, naming the columns that changed and
                # never their values -- the same rule the audit log
                # follows, for the same reason: two of those columns are a
                # paid credential.
                "results": [
                    {
                        "action": item.action,
                        "external_id": item.external_id,
                        "endpoint": item.endpoint,
                        "proxy": item.proxy_id,
                        "fields": list(item.fields),
                        "detail": item.detail or None,
                    }
                    for item in outcome.outcomes
                ],
            }
        )

    @action(
        detail=False,
        methods=["post"],
        url_path="upload_csv",
        parser_classes=(MultiPartParser, FormParser),
    )
    def upload_csv(self, request):
        """`multipart/form-data` field `file` -> 201 + the batch tally.

        Parsing is `proxies.importers.import_proxies_from_csv`, unchanged.
        It was deliberately kept out of `admin.py` so a second caller would
        never need to re-implement the header aliasing, the per-row commit
        and the premium-by-default tier rule -- which is why removing
        Django Admin cost this endpoint nothing. **This is now the only
        upload path**; the `ProxyAdmin.get_urls` -> `upload_csv_view` page
        and `proxies.forms.ProxyCSVUploadForm` are gone, and the React
        console has always called here.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data["file"]

        # The 5 MB cap. It used to live in `proxies.forms`, a Django
        # *admin form* this endpoint never touched; that form has since
        # been deleted along with the console, so this is the only place
        # the cap exists. DATA_UPLOAD_MAX_MEMORY_SIZE does not apply to
        # uploaded files, so nothing else enforces it. Checked before
        # `import_proxies_from_csv` reads the file -- the importer holds
        # the whole thing in memory twice (bytes, then decoded StringIO),
        # so the check has to happen before the read, not inside it.
        max_bytes = getattr(settings, "PROXY_CSV_MAX_BYTES", 5242880)
        if upload.size and upload.size > max_bytes:
            return Response(
                {
                    "error": {
                        "code": "csv_too_large",
                        "status": status.HTTP_400_BAD_REQUEST,
                        "detail": {
                            "message": f"File exceeds the {max_bytes} byte limit.",
                            "size": upload.size,
                            "max_bytes": max_bytes,
                        },
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # `UploadedFile.name` is already basenamed by Django, so a
        # traversal-shaped filename cannot escape; `os.path.basename` here
        # is belt and braces for the day someone adds file retention.
        filename = os.path.basename(getattr(upload, "name", "") or "upload.csv")
        if not filename.lower().endswith(CSV_EXTENSIONS):
            return Response(
                {
                    "error": {
                        "code": "csv_invalid",
                        "status": status.HTTP_400_BAD_REQUEST,
                        "detail": (
                            "Expected a .csv or .txt file; got "
                            f"{filename!r}."
                        ),
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            batch = import_proxies_from_csv(
                upload,
                filename=filename,
                uploaded_by=request.user,
            )
        except CSVImportError as exc:
            return Response(
                {
                    "error": {
                        "code": "csv_invalid",
                        "status": status.HTTP_400_BAD_REQUEST,
                        "detail": str(exc),
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        audit(
            "admin_csv_upload",
            request=request,
            level=logging.WARNING,
            batch_id=batch.pk,
            filename=batch.filename,
            row_count=batch.row_count,
            created_count=batch.created_count,
            updated_count=batch.updated_count,
            fail_count=batch.fail_count,
        )
        return Response(
            {
                "batch_id": batch.pk,
                "row_count": batch.row_count,
                "created_count": batch.created_count,
                "updated_count": batch.updated_count,
                "success_count": batch.success_count,
                "fail_count": batch.fail_count,
                "errors": parse_batch_errors(batch.errors),
            },
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Upload batches (read-only)
# ---------------------------------------------------------------------------


class AdminUploadBatchViewSet(AdminReadOnlyViewSet):
    serializer_class = AdminUploadBatchSerializer
    filterset_class = AdminUploadBatchFilter
    search_fields = ("filename", "uploaded_by__email")
    ordering_fields = ("created_at", "row_count", "fail_count", "id")
    ordering = ("-created_at",)
    queryset = UploadBatch.objects.select_related("uploaded_by")


class AdminProviderSyncRunViewSet(AdminReadOnlyViewSet):
    """Sync history. The `upload-batches` of provider syncs.

    Read-only, and not merely by convention: a run row is a record of what
    happened, and an editable one is not a record. It also carries the
    only operator-visible answer to "has the 4am job been working", which
    is a question nobody asks until the inventory has already decayed.
    """

    serializer_class = AdminProviderSyncRunSerializer
    filterset_class = AdminProviderSyncRunFilter
    search_fields = ("provider", "source", "errors")
    ordering_fields = (
        "started_at",
        "finished_at",
        "fetched_count",
        "created_count",
        "disabled_count",
        "failed_count",
        "id",
    )
    ordering = ("-started_at", "-id")
    queryset = ProviderSyncRun.objects.select_related("triggered_by")


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


class AdminContentViewSet(AdminModelViewSet):
    """Unlike `api.views.ContentViewSet`, this sees unpublished rows -- that
    is the point of an editor -- and is keyed on `id`, not `key`."""

    serializer_class = AdminContentSerializer
    filterset_class = AdminContentFilter
    search_fields = ("key", "title", "body")
    ordering_fields = ("priority", "created_at", "updated_at", "key", "id")
    ordering = ("-priority", "-created_at")
    queryset = Content.objects.all()


# ---------------------------------------------------------------------------
# Usage logs (read-only)
# ---------------------------------------------------------------------------


class AdminUsageLogViewSet(AdminReadOnlyViewSet):
    serializer_class = AdminUsageLogSerializer
    filterset_class = AdminUsageLogFilter
    search_fields = ("user__email",)
    ordering_fields = ("session_start", "session_end", "bytes_up", "bytes_down", "id")
    ordering = ("-session_start",)
    queryset = UsageLog.objects.select_related("user")


# ---------------------------------------------------------------------------
# Audit log (read-only)
# ---------------------------------------------------------------------------


class AdminAuditLogViewSet(AdminReadOnlyViewSet):
    """`GET /api/admin/audit-log/` -- the durable trail (P9).

    Read-only at every level, and that is four independent layers rather
    than one: `ReadOnlyModelViewSet` routes no write verbs, the queryset
    refuses `update`/`delete`, the model refuses them on the instance, and
    a database trigger refuses them underneath all of it. Any one of those
    being removed by a later change still leaves three.

    Deliberately **not** a `ModelViewSet` with permissions tightened. A
    router registers create/update/destroy routes for a ModelViewSet
    whatever the permissions say, and `adminapi.checks` verifies staff
    access, not verb set -- so the destroy route would exist, reachable by
    exactly the staff account an audit trail is meant to hold to account.

    Staff-only, like everything here. Not superuser-only: a trail only
    staff with the highest privilege can read is a trail that does not
    deter the people most able to abuse it, and `is_staff` is already the
    boundary for reading every row this table describes.
    """

    serializer_class = AdminAuditLogSerializer
    filterset_class = AdminAuditLogFilter
    search_fields = ("actor_email", "action", "target_id")
    ordering_fields = ("created_at", "action", "actor_email", "id")
    ordering = ("-created_at",)
    queryset = AdminAuditLog.objects.select_related("actor")


# ---------------------------------------------------------------------------
# Chains (read-only)
# ---------------------------------------------------------------------------


class AdminChainViewSet(AdminReadOnlyViewSet):
    serializer_class = AdminChainSerializer
    filterset_class = AdminChainFilter
    search_fields = ("name", "user__email")
    ordering_fields = ("created_at", "updated_at", "name", "id")
    ordering = ("-created_at",)
    queryset = ProxyChain.objects.select_related("user").prefetch_related(
        Prefetch(
            "hops",
            queryset=ChainHop.objects.select_related("proxy").order_by("position"),
        )
    )

    @action(detail=True, methods=["get"], url_path="diversity")
    def diversity(self, request, pk=None):
        """`GET /api/admin/chains/{id}/diversity/` -- what this user's
        chain actually buys them.

        The same analysis the mobile client gets, with two differences.
        Real vendor names are shown rather than "Vendor A": the operator
        is the one who typed them in. And the chain is **not** resolved
        first -- an admin read must not depend on the subscriber's
        entitlement being currently valid, and constrained positions are
        reported honestly as unresolved rather than filled in with
        whatever the operator's own plan would pick.
        """
        chain = self.get_object()
        report = analyse_chain(chain)
        payload = report.as_dict()
        payload["chain"] = {
            "id": chain.pk,
            "name": chain.name,
            "user": chain.user_id,
        }
        payload["analysed_at"] = timezone.now()
        return Response(payload)


# ---------------------------------------------------------------------------
# IP intelligence (R2)
# ---------------------------------------------------------------------------


class AdminIntelViewSet(AdminReadOnlyViewSet):
    """Cached origin lookups, plus the two actions that create them.

    `list` and `retrieve` are an ordinary admin read of the cache.
    `candidates` and `refresh` reach the network, synchronously, against a
    rate-limited third party -- so they carry their own throttle and their
    own audit events rather than riding the generic admin budget.
    """

    serializer_class = AdminIpIntelRecordSerializer
    filterset_class = AdminIpIntelFilter
    search_fields = ("query", "asn_org", "prefix", "ip")
    ordering_fields = ("query", "asn", "country", "looked_up_at", "expires_at", "id")
    ordering = ("query",)
    queryset = IpIntelRecord.objects.all()

    def get_serializer_class(self):
        if getattr(self, "action", None) == "candidates":
            return IntelCandidateAnalysisSerializer
        if getattr(self, "action", None) == "refresh":
            return IntelRefreshSerializer
        return AdminIpIntelRecordSerializer

    def get_throttles(self):
        if getattr(self, "action", None) in ("candidates", "refresh"):
            return [AdminIntelThrottle()]
        return super().get_throttles()

    @action(detail=False, methods=["post"], url_path="candidates")
    def candidates(self, request):
        """`POST /api/admin/intel/candidates/` -- three vendors, or one?

        The question this whole feature was asked for: paste in candidate
        endpoints from several sellers and find out whether they actually
        represent different operators **before** paying for a year of all
        three. The proxy market is consolidated and resellers routinely
        front the same upstream network, so "three vendors" and "three
        operators" are different claims and only one of them is checkable.

        The hosts deliberately **need not exist in the inventory**. Nothing
        is written to `Proxy`; the only rows created are cache entries in
        `IpIntelRecord`, which are facts about addresses rather than
        inventory the operator has committed to.

        Synchronous for the same reason the probe endpoint is, and capped
        harder: `IP_INTEL_API_MAX_HOSTS` candidates, a per-lookup timeout,
        serial lookups under the configured rate limit.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        candidates = serializer.validated_data["candidates"]
        force = serializer.validated_data["refresh"]

        hosts = [candidate["host"] for candidate in candidates]
        batch = lookup_hosts(hosts, intel=get_ip_intel(), force=force)

        entries = [
            HopInput(
                position=index,
                host=candidate["host"],
                provider=candidate.get("provider") or "",
                country=candidate.get("country") or "",
                label=candidate.get("label") or "",
            )
            for index, candidate in enumerate(candidates)
        ]
        # Re-read through the cache rather than using the batch's records
        # directly, so a host that was already cached and one that was
        # just looked up are joined by exactly the same code path.
        report = analyse_hops(entries, records=cached_facts(hosts))

        audit(
            "admin_intel_candidates",
            request=request,
            candidates=len(candidates),
            hosts=hosts,
            looked_up=len(batch.looked_up),
            from_cache=len(batch.from_cache),
            failed=len(batch.failed),
            score=round(report.score, 1),
            worst_severity=report.worst_severity,
            finding_codes=[finding.code for finding in report.findings],
            forced=force,
        )

        payload = report.as_dict()
        payload["analysed_at"] = timezone.now()
        payload["lookups"] = {
            "requested": len(hosts),
            "looked_up": batch.looked_up,
            "from_cache": batch.from_cache,
            "failed": batch.failed,
            # Set when the source cannot be used at all -- no resolver
            # configured, say. Distinct from per-host failure: it means
            # nothing was checked, not that these hosts are unusual.
            "unavailable": batch.unavailable or None,
        }
        return Response(payload)

    @action(detail=False, methods=["post"], url_path="refresh")
    def refresh(self, request):
        """`POST /api/admin/intel/refresh/` -- look these up now.

        `{ids: [...]}` for inventory rows, `{hosts: [...]}` for anything
        else, `force: true` to ignore a still-fresh cache entry. The
        normal path is the scheduled `refresh_ip_intel` command; this is
        the button beside a filtered table.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data.get("ids") or []
        hosts = list(serializer.validated_data.get("hosts") or [])
        force = serializer.validated_data["force"]

        found_ids = []
        if ids:
            rows = Proxy.objects.filter(pk__in=ids).values_list("pk", "host")
            for pk, host in rows:
                found_ids.append(pk)
                hosts.append(host)

        unique_hosts = list(dict.fromkeys(normalise_host(host) for host in hosts))
        cap = int(getattr(settings, "IP_INTEL_API_MAX_HOSTS", 25))
        if len(unique_hosts) > cap:
            # The ids cap is MAX_IDS, but a thousand proxies can be a
            # thousand distinct hosts and this runs in a request worker.
            # Refusing is better than a request that times out halfway
            # through and leaves the operator guessing what was written.
            raise ValidationError(
                {
                    "ids": (
                        f"That selection resolves to {len(unique_hosts)} "
                        f"distinct hosts; at most {cap} can be refreshed "
                        f"per request. Use the `refresh_ip_intel` "
                        f"management command for a bulk refresh."
                    )
                }
            )

        batch = lookup_hosts(unique_hosts, intel=get_ip_intel(), force=force)

        audit(
            "admin_intel_refresh",
            request=request,
            proxy_ids=found_ids,
            requested_hosts=len(unique_hosts),
            looked_up=len(batch.looked_up),
            from_cache=len(batch.from_cache),
            failed=len(batch.failed),
            forced=force,
        )

        records = [
            batch.records[key] for key in unique_hosts if key in batch.records
        ]
        return Response(
            {
                "requested": len(unique_hosts),
                "looked_up": batch.looked_up,
                "from_cache": batch.from_cache,
                "failed": batch.failed,
                "unavailable": batch.unavailable or None,
                "not_found": sorted(set(ids) - set(found_ids)),
                "records": AdminIpIntelRecordSerializer(records, many=True).data,
            }
        )
