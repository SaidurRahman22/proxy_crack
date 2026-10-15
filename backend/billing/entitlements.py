"""Server-side entitlement resolution.

This module is the single place that answers "what is this user allowed to
see and do". Views must not reimplement any part of it, and the client is
never consulted. See `Project/graph.md` section 4.

Resolution order, matching that flowchart:

    active subscription -> its plan's limits
    no active subscription -> the free plan's limits
    no free plan row at all -> settings.ENTITLEMENT_FALLBACK

The last step exists so a missing or mis-seeded database degrades to the
tightest limits rather than failing open.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from proxies.models import ProxyTier, tiers_up_to

FREE_FALLBACK_PERIOD_DAYS = 30


@dataclass(frozen=True)
class Entitlement:
    """An immutable snapshot of what a user may do right now."""

    plan_code: str
    plan_name: str
    is_free_tier: bool
    source: str  # "subscription" | "free_plan" | "fallback"
    max_chain_length: int
    max_devices: int
    bandwidth_limit_bytes: int
    max_proxy_tier: str
    allowed_proxy_tiers: Tuple[str, ...]
    subscription_id: Optional[int] = None
    subscription_status: Optional[str] = None
    expires_at: Optional[datetime] = None
    period_start: Optional[datetime] = None

    def allows_tier(self, tier: str) -> bool:
        return str(tier) in self.allowed_proxy_tiers

    def allows_chain_length(self, length: int) -> bool:
        return 1 <= length <= self.max_chain_length


def _fallback_entitlement() -> Entitlement:
    cfg = settings.ENTITLEMENT_FALLBACK
    tier = cfg.get("proxy_tier", ProxyTier.FREE)
    return Entitlement(
        plan_code="fallback",
        plan_name="Free (fallback)",
        is_free_tier=True,
        source="fallback",
        max_chain_length=int(cfg.get("max_chain_length", 1)),
        max_devices=int(cfg.get("max_devices", 1)),
        bandwidth_limit_bytes=int(cfg.get("bandwidth_limit_bytes", 0)),
        max_proxy_tier=str(tier),
        allowed_proxy_tiers=tiers_up_to(tier),
        period_start=timezone.now() - timedelta(days=FREE_FALLBACK_PERIOD_DAYS),
    )


def _from_plan(plan, source, subscription=None) -> Entitlement:
    return Entitlement(
        plan_code=plan.code,
        plan_name=plan.name,
        is_free_tier=plan.is_free_tier,
        source=source,
        max_chain_length=plan.max_chain_length,
        max_devices=plan.max_devices,
        bandwidth_limit_bytes=plan.bandwidth_limit_bytes,
        max_proxy_tier=str(plan.max_proxy_tier),
        allowed_proxy_tiers=tiers_up_to(plan.max_proxy_tier),
        subscription_id=subscription.pk if subscription else None,
        subscription_status=subscription.status if subscription else None,
        expires_at=subscription.expires_at if subscription else None,
        period_start=(
            subscription.started_at
            if subscription
            else timezone.now() - timedelta(days=FREE_FALLBACK_PERIOD_DAYS)
        ),
    )


def get_free_plan():
    from billing.models import Plan

    return (
        Plan.objects.filter(is_free_tier=True, is_active=True)
        .order_by("sort_order", "pk")
        .first()
    )


def get_active_subscription(user):
    """The user's current subscription, or None.

    Expiry is evaluated against the clock, not just the `status` column, so a
    row that nothing has swept yet cannot keep granting premium access.
    """
    from billing.models import Subscription, SubscriptionStatus

    if not user or not user.is_authenticated:
        return None
    subscription = (
        Subscription.objects.filter(user=user, status=SubscriptionStatus.ACTIVE)
        .select_related("plan")
        .order_by("-started_at")
        .first()
    )
    if subscription and subscription.is_current():
        return subscription
    return None


def resolve_entitlement(user) -> Entitlement:
    """The only entitlement entry point. Never raises."""
    subscription = get_active_subscription(user)
    if subscription is not None:
        return _from_plan(subscription.plan, "subscription", subscription)

    free_plan = get_free_plan()
    if free_plan is not None:
        return _from_plan(free_plan, "free_plan")

    return _fallback_entitlement()


def bandwidth_used_bytes(user, since=None) -> int:
    """Bytes reported by the user in the current entitlement period."""
    from billing.models import UsageLog

    if not user or not user.is_authenticated:
        return 0
    queryset = UsageLog.objects.filter(user=user)
    if since is not None:
        queryset = queryset.filter(session_start__gte=since)
    totals = queryset.aggregate(up=Sum("bytes_up"), down=Sum("bytes_down"))
    return (totals["up"] or 0) + (totals["down"] or 0)


def entitlement_payload(user, entitlement=None) -> dict:
    """The dict served by GET /api/subscription/ and echoed to the client.

    The client renders these numbers; it does not enforce them.
    """
    entitlement = entitlement or resolve_entitlement(user)
    used = bandwidth_used_bytes(user, since=entitlement.period_start)
    limit = entitlement.bandwidth_limit_bytes
    return {
        "max_chain_length": entitlement.max_chain_length,
        "max_devices": entitlement.max_devices,
        "allowed_proxy_tiers": list(entitlement.allowed_proxy_tiers),
        "max_proxy_tier": entitlement.max_proxy_tier,
        "bandwidth_limit_bytes": limit,
        "bandwidth_used_bytes": used,
        "bandwidth_remaining_bytes": None if limit == 0 else max(limit - used, 0),
        "bandwidth_period_start": entitlement.period_start,
        "is_free_tier": entitlement.is_free_tier,
        "source": entitlement.source,
    }
