"""Filtering for the admin list endpoints.

Unlike `api.filters`, nothing here narrows an entitlement scope -- the admin
API is unscoped by design and these are purely the operator's table filters.

`IdsFilterBackend` is global to the admin API: the contract promises
`?ids=1,2,3` on *every* list endpoint so the SPA can round-trip a bulk
selection (select rows, act on them, re-fetch exactly those rows) without a
per-resource filter having to remember to offer it.
"""

import django_filters
from django.contrib.auth import get_user_model
from rest_framework.exceptions import ValidationError
from rest_framework.filters import BaseFilterBackend

from billing.models import (
    Plan,
    Subscription,
    SubscriptionPlatform,
    SubscriptionStatus,
    UsageLog,
)
from adminapi.models import AdminAuditLog
from core.models import Content, ContentKind
from proxies.models import Proxy, ProxyChain, ProxyProtocol, ProxyTier, UploadBatch

User = get_user_model()

# A selection round-trip is bounded by what the page size allows plus slack.
MAX_IDS = 1000


class IdsFilterBackend(BaseFilterBackend):
    """`?ids=1,2,3` -> `pk__in=[1, 2, 3]`."""

    def filter_queryset(self, request, queryset, view):
        raw = request.query_params.get("ids")
        if raw is None:
            return queryset

        parts = [part.strip() for part in raw.split(",") if part.strip()]
        if not parts:
            return queryset.none()
        if len(parts) > MAX_IDS:
            raise ValidationError({"ids": f"At most {MAX_IDS} ids per request."})

        try:
            ids = [int(part) for part in parts]
        except ValueError:
            raise ValidationError({"ids": "Must be a comma-separated list of integers."})
        return queryset.filter(pk__in=ids)

    def get_schema_operation_parameters(self, view):
        return [
            {
                "name": "ids",
                "required": False,
                "in": "query",
                "description": "Comma-separated primary keys.",
                "schema": {"type": "string"},
            }
        ]


class AdminUserFilter(django_filters.FilterSet):
    is_active = django_filters.BooleanFilter(field_name="is_active")
    is_staff = django_filters.BooleanFilter(field_name="is_staff")
    is_superuser = django_filters.BooleanFilter(field_name="is_superuser")

    class Meta:
        model = User
        fields = ["is_active", "is_staff", "is_superuser"]


class AdminPlanFilter(django_filters.FilterSet):
    class Meta:
        model = Plan
        fields = ["is_active", "is_free_tier"]


class AdminSubscriptionFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(choices=SubscriptionStatus.choices)
    platform = django_filters.ChoiceFilter(choices=SubscriptionPlatform.choices)
    plan = django_filters.ModelChoiceFilter(queryset=Plan.objects.all())
    user = django_filters.ModelChoiceFilter(queryset=User.objects.all())

    class Meta:
        model = Subscription
        fields = ["status", "platform", "plan", "user"]


class AdminProxyFilter(django_filters.FilterSet):
    protocol = django_filters.ChoiceFilter(choices=ProxyProtocol.choices)
    tier = django_filters.ChoiceFilter(choices=ProxyTier.choices)
    country = django_filters.CharFilter(field_name="country", lookup_expr="iexact")
    city = django_filters.CharFilter(field_name="city", lookup_expr="iexact")
    is_alive = django_filters.BooleanFilter(field_name="is_alive")
    is_enabled = django_filters.BooleanFilter(field_name="is_enabled")
    upload_batch = django_filters.ModelChoiceFilter(queryset=UploadBatch.objects.all())
    max_latency_ms = django_filters.NumberFilter(field_name="latency_ms", lookup_expr="lte")

    # Health. `unprobed` is the one an operator reaches for first after an
    # import -- "what has nothing measured yet" is a different and more
    # actionable question than "what is dead".
    max_p95_latency_ms = django_filters.NumberFilter(
        field_name="latency_p95_ms", lookup_expr="lte"
    )
    min_success_rate = django_filters.NumberFilter(
        field_name="success_rate", lookup_expr="gte"
    )
    max_success_rate = django_filters.NumberFilter(
        field_name="success_rate", lookup_expr="lte"
    )
    min_quality_score = django_filters.NumberFilter(
        field_name="quality_score", lookup_expr="gte"
    )
    max_quality_score = django_filters.NumberFilter(
        field_name="quality_score", lookup_expr="lte"
    )
    min_consecutive_failures = django_filters.NumberFilter(
        field_name="consecutive_failures", lookup_expr="gte"
    )
    unprobed = django_filters.BooleanFilter(
        field_name="probe_count",
        lookup_expr="exact",
        method="filter_unprobed",
        label="Only proxies with no probes in the rolling window",
    )
    checked_before = django_filters.IsoDateTimeFilter(
        field_name="last_checked", lookup_expr="lt"
    )

    def filter_unprobed(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(probe_count=0) if value else queryset.exclude(
            probe_count=0
        )

    class Meta:
        model = Proxy
        # username / password / ss_method are EncryptedCharField and reject
        # every lookup but isnull -- they can never appear here.
        fields = [
            "protocol",
            "tier",
            "country",
            "city",
            "is_alive",
            "is_enabled",
            "upload_batch",
            "max_latency_ms",
            "max_p95_latency_ms",
            "min_success_rate",
            "max_success_rate",
            "min_quality_score",
            "max_quality_score",
            "min_consecutive_failures",
            "unprobed",
            "checked_before",
        ]


class AdminUploadBatchFilter(django_filters.FilterSet):
    uploaded_by = django_filters.ModelChoiceFilter(queryset=User.objects.all())
    created_at = django_filters.DateTimeFromToRangeFilter(field_name="created_at")

    class Meta:
        model = UploadBatch
        fields = ["uploaded_by", "created_at"]


class AdminContentFilter(django_filters.FilterSet):
    kind = django_filters.ChoiceFilter(choices=ContentKind.choices)
    is_published = django_filters.BooleanFilter(field_name="is_published")

    class Meta:
        model = Content
        fields = ["kind", "is_published"]


class AdminUsageLogFilter(django_filters.FilterSet):
    user = django_filters.ModelChoiceFilter(queryset=User.objects.all())
    # Yields ?session_start_after= and ?session_start_before= (inclusive).
    session_start = django_filters.DateTimeFromToRangeFilter(field_name="session_start")

    class Meta:
        model = UsageLog
        fields = ["user", "session_start"]


class AdminChainFilter(django_filters.FilterSet):
    user = django_filters.ModelChoiceFilter(queryset=User.objects.all())

    class Meta:
        model = ProxyChain
        fields = ["user"]


class AdminAuditLogFilter(django_filters.FilterSet):
    """Filters for the audit trail: who, what, and when.

    `actor` is the FK and `actor_email` is the denormalised copy, and both
    are filterable on purpose. Once an account is deleted the FK is null
    and only the email can find that person's history -- an audit log you
    cannot query after the actor leaves answers the wrong half of the
    question.
    """

    actor = django_filters.ModelChoiceFilter(queryset=User.objects.all())
    actor_email = django_filters.CharFilter(
        field_name="actor_email", lookup_expr="iexact"
    )
    action = django_filters.CharFilter(field_name="action", lookup_expr="exact")
    target_type = django_filters.CharFilter(
        field_name="target_type", lookup_expr="exact"
    )
    target_id = django_filters.CharFilter(field_name="target_id", lookup_expr="exact")
    # Yields ?created_at_after= and ?created_at_before=.
    created_at = django_filters.IsoDateTimeFromToRangeFilter(field_name="created_at")

    class Meta:
        model = AdminAuditLog
        fields = [
            "actor",
            "actor_email",
            "action",
            "target_type",
            "target_id",
            "created_at",
        ]
