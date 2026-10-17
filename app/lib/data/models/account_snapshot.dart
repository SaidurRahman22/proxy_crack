import 'package:flutter/foundation.dart';

import 'entitlements.dart';
import 'plan.dart';
import 'subscription.dart';

/// Everything `GET /api/subscription/` answers, in one object.
///
/// The endpoint returns four things at once — the current plan, the
/// subscription row (which is **null** on the free tier), the resolved
/// entitlements, and the plan catalogue. The original repository interface
/// asked for plans and subscription separately, which would have meant two
/// round trips for one server response. Fetching the snapshot once and
/// deriving both is why `CatalogRepository` now exposes `fetchAccount()`;
/// the screens still read `controller.plans` and `controller.subscription`
/// and are unaware of the change.
@immutable
class AccountSnapshot {
  const AccountSnapshot({
    required this.plan,
    required this.subscription,
    required this.entitlements,
    required this.availablePlans,
    this.hasSubscriptionRow = false,
  });

  /// The plan the entitlements were resolved from.
  final Plan plan;

  /// The user's subscription.
  ///
  /// Synthesised from [plan] when the server sent `"subscription": null`,
  /// which is the normal free-tier case — the screens need *something* with
  /// a status and a quota to render, and inventing a sentinel inside each
  /// screen would be worse than doing it once here. [hasSubscriptionRow]
  /// says which it is.
  final Subscription subscription;

  final Entitlements entitlements;

  /// `Plan.objects.filter(is_active=True)` — the paywall catalogue.
  final List<Plan> availablePlans;

  /// False when the free tier was synthesised rather than read.
  final bool hasSubscriptionRow;

  /// The limit the UI should present. Taken from the entitlements rather
  /// than the plan: the server resolves them and they are what it enforces.
  int get maxChainLength => entitlements.maxChainLength;

  factory AccountSnapshot.fromJson(Map<String, dynamic> json) {
    final planJson = json['plan'] as Map<String, dynamic>?;
    final plan = planJson == null
        ? _unknownPlan
        : Plan.fromJson(planJson);

    final entitlements = Entitlements.fromJson(
      (json['entitlements'] as Map<String, dynamic>?) ??
          const <String, dynamic>{},
    );

    final subscriptionJson = json['subscription'] as Map<String, dynamic>?;
    final subscription = subscriptionJson == null
        ? Subscription.synthesisedFree(
            plan: plan,
            bytesUsed: entitlements.bandwidthUsedBytes,
            startedAt: entitlements.bandwidthPeriodStart,
          )
        : Subscription.fromJson(
            subscriptionJson,
            fallbackPlan: plan,
            bytesUsed: entitlements.bandwidthUsedBytes,
          );

    return AccountSnapshot(
      plan: plan,
      subscription: subscription,
      entitlements: entitlements,
      availablePlans:
          (json['available_plans'] as List<dynamic>? ?? const <dynamic>[])
              .whereType<Map<String, dynamic>>()
              .map(Plan.fromJson)
              .toList(growable: false),
      hasSubscriptionRow: subscriptionJson != null,
    );
  }

  /// Degenerate plan for the case the server could not name one — only
  /// reachable when the database has no free plan at all, which the backend
  /// treats as an operator fault (`ENTITLEMENT_FALLBACK`).
  static const Plan _unknownPlan = Plan(
    id: 0,
    code: 'fallback',
    name: 'Limited access',
    priceCents: 0,
    currency: 'USD',
    durationDays: 0,
    maxChainLength: 1,
    maxDevices: 1,
    bandwidthLimitBytes: 0,
    isFreeTier: true,
    tagline: 'No plan is configured on the server, so the tightest limits '
        'apply.',
  );
}
