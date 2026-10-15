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
    AdminCredentialDailyThrottle,
    AdminCredentialHourlyThrottle,
    AdminCSVUploadThrottle,
    AdminLoginEmailThrottle,
    AdminLoginIPThrottle,
)
from adminapi.filters import (
    MAX_IDS,
    AdminChainFilter,
    AdminContentFilter,
    AdminPlanFilter,
    AdminProxyFilter,
    AdminSubscriptionFilter,
    AdminUploadBatchFilter,
    AdminUsageLogFilter,
    AdminUserFilter,
    IdsFilterBackend,
)
from adminapi.pagination import AdminPagination
from adminapi.permissions import IsStaffUser
from adminapi.serializers import (
    AdminChainSerializer,
    AdminContentSerializer,
    AdminMeSerializer,
    AdminPlanSerializer,
    AdminProxyDetailSerializer,
    AdminProxyListSerializer,
    AdminSubscriptionSerializer,
    AdminUploadBatchSerializer,
    AdminUsageLogSerializer,
    AdminUserSerializer,
    BulkDeleteSerializer,
    BulkEnableSerializer,
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
from proxies.importers import CSVImportError, import_proxies_from_csv
from proxies.models import (
    ChainHop,
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
    fail in a way that strands a session. The failure is logged.
    """

    serializer_class = LogoutSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data["refresh"]).blacklist()
        except TokenError as exc:
            logger.info("admin_logout_token_unusable user=%s (%s)", request.user.pk, exc)
        else:
            logger.info("admin_logout user=%s", request.user.pk)
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
        then sign in at `/admin/`, which can edit groups and permissions
        this API does not even expose. So resetting a *peer's* password is
        a superuser action, and resetting a superuser's password is a
        superuser action. Your own is always yours.
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
    ordering_fields = ("latency_ms", "last_checked", "host", "country", "id")
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

    @action(
        detail=False,
        methods=["post"],
        url_path="upload_csv",
        parser_classes=(MultiPartParser, FormParser),
    )
    def upload_csv(self, request):
        """`multipart/form-data` field `file` -> 201 + the batch tally.

        Parsing is `proxies.importers.import_proxies_from_csv`, unchanged --
        the same routine the Django Admin upload uses. Re-implementing the
        header aliasing, the per-row commit and the premium-by-default tier
        rule here would mean two parsers drifting apart.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data["file"]

        # The 5 MB cap that exists today lives in `proxies.forms`, a Django
        # *admin form* this endpoint never touches, and
        # DATA_UPLOAD_MAX_MEMORY_SIZE does not apply to uploaded files. So
        # it is re-enforced here, and before `import_proxies_from_csv`
        # reads the file -- the importer holds the whole thing in memory
        # twice (bytes, then decoded StringIO), so the check has to happen
        # before the read, not inside it.
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
