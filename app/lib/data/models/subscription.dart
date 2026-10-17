import 'package:flutter/foundation.dart';

import 'plan.dart';

/// `SUBSCRIPTION.status` = `active|expired|cancelled`.
enum SubscriptionStatus {
  active('active', 'Active'),
  expired('expired', 'Expired'),
  cancelled('cancelled', 'Cancelled');

  const SubscriptionStatus(this.wire, this.label);

  final String wire;
  final String label;

  static SubscriptionStatus fromWire(String value) =>
      SubscriptionStatus.values.firstWhere(
        (s) => s.wire == value,
        orElse: () => SubscriptionStatus.expired,
      );
}

/// A user's current entitlement. Maps to `SUBSCRIPTION` in graph.md section 3.
///
/// This is a *cache of a server decision*, not the decision itself. The
/// entitlement flow in graph.md section 4 runs on every `/api/proxies/` call.
@immutable
class Subscription {
  const Subscription({
    required this.id,
    required this.userId,
    required this.plan,
    required this.startedAt,
    required this.expiresAt,
    required this.status,
    this.platformTxnId,
    this.bytesUsed = 0,
  });

  /// `SUBSCRIPTION.id`
  final int id;

  /// `SUBSCRIPTION.user_id`
  final int userId;

  /// Resolved `SUBSCRIPTION.plan_id`.
  final Plan plan;

  /// `SUBSCRIPTION.started_at`
  final DateTime startedAt;

  /// `SUBSCRIPTION.expires_at`
  final DateTime expiresAt;

  /// `SUBSCRIPTION.status`
  final SubscriptionStatus status;

  /// `SUBSCRIPTION.platform_txn_id` -- the Play purchase token. Present only
  /// for paid plans and never rendered in the UI.
  final String? platformTxnId;

  /// Rolled up from `USAGELOG`. Shown as a quota meter on the paywall.
  final int bytesUsed;

  bool get isActive => status == SubscriptionStatus.active;

  /// The limit the UI should *display*. The server still has the final say.
  int get effectiveMaxChainLength => isActive ? plan.maxChainLength : 1;

  int get daysRemaining {
    if (!isActive) return 0;
    final diff = expiresAt.difference(DateTime.now()).inDays;
    return diff < 0 ? 0 : diff;
  }

  /// 0.0-1.0, or null when the plan is unmetered.
  double? get bandwidthUsedFraction {
    if (plan.isUnmetered) return null;
    if (plan.bandwidthLimitBytes <= 0) return null;
    return (bytesUsed / plan.bandwidthLimitBytes).clamp(0.0, 1.0);
  }

  factory Subscription.freeTier(Plan freePlan) => Subscription(
        id: 0,
        userId: 0,
        plan: freePlan,
        startedAt: DateTime.now(),
        expiresAt: DateTime.now().add(const Duration(days: 3650)),
        status: SubscriptionStatus.active,
      );

  /// The free tier, which has **no `Subscription` row on the server**.
  ///
  /// `GET /api/subscription/` returns `"subscription": null` for a user with
  /// no active purchase; the plan and the entitlements are still populated.
  /// Rather than make every screen handle a null subscription, the free case
  /// is materialised here once, with `hasSubscriptionRow = false` on the
  /// snapshot recording that it was synthesised.
  factory Subscription.synthesisedFree({
    required Plan plan,
    int bytesUsed = 0,
    DateTime? startedAt,
  }) {
    final now = DateTime.now();
    return Subscription(
      id: 0,
      userId: 0,
      plan: plan,
      startedAt: startedAt ?? now,
      // Not a real expiry: the free tier does not end. Far enough out that
      // `daysRemaining` never counts down to a deadline that does not exist.
      expiresAt: now.add(const Duration(days: 3650)),
      status: SubscriptionStatus.active,
      bytesUsed: bytesUsed,
    );
  }

  Subscription copyWith({
    Plan? plan,
    SubscriptionStatus? status,
    DateTime? expiresAt,
    int? bytesUsed,
  }) =>
      Subscription(
        id: id,
        userId: userId,
        plan: plan ?? this.plan,
        startedAt: startedAt,
        expiresAt: expiresAt ?? this.expiresAt,
        status: status ?? this.status,
        platformTxnId: platformTxnId,
        bytesUsed: bytesUsed ?? this.bytesUsed,
      );

  /// Parses `SubscriptionSerializer`.
  ///
  /// The server's row carries `{id, plan, started_at, expires_at, status,
  /// platform, auto_renewing}` — no `user_id` (it is the token's user by
  /// construction) and no byte total (usage is aggregated into the
  /// entitlement payload, not onto the subscription). Both are supplied by
  /// the caller from the surrounding response.
  factory Subscription.fromJson(
    Map<String, dynamic> json, {
    Plan? fallbackPlan,
    int bytesUsed = 0,
  }) {
    final planJson = json['plan'] as Map<String, dynamic>?;
    final plan = planJson != null ? Plan.fromJson(planJson) : fallbackPlan;
    assert(
      plan != null,
      'A subscription needs a plan: pass fallbackPlan when the row omits it.',
    );
    return Subscription(
      id: (json['id'] as num?)?.toInt() ?? 0,
      userId: (json['user_id'] as num?)?.toInt() ?? 0,
      plan: plan ?? fallbackPlan!,
      startedAt:
          DateTime.tryParse(json['started_at'] as String? ?? '') ??
              DateTime.now(),
      expiresAt:
          DateTime.tryParse(json['expires_at'] as String? ?? '') ??
              DateTime.now(),
      status: SubscriptionStatus.fromWire(
        json['status'] as String? ?? 'expired',
      ),
      platformTxnId: json['platform_txn_id'] as String?,
      bytesUsed: (json['bytes_used'] as num?)?.toInt() ?? bytesUsed,
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'id': id,
        'user_id': userId,
        'plan': plan.toJson(),
        'started_at': startedAt.toIso8601String(),
        'expires_at': expiresAt.toIso8601String(),
        'status': status.wire,
        'bytes_used': bytesUsed,
      };

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is Subscription &&
          other.id == id &&
          other.plan == plan &&
          other.status == status &&
          other.bytesUsed == bytesUsed);

  @override
  int get hashCode => Object.hash(id, plan, status, bytesUsed);
}
