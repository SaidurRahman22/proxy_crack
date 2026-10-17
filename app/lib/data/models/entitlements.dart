import 'package:flutter/foundation.dart';

/// What the server will actually let this account do, right now.
///
/// `GET /api/subscription/` returns this alongside the plan, and
/// `billing.entitlements.entitlement_payload` is the only thing that
/// produces it. **The client renders these numbers; it does not enforce
/// them.** Every write is re-checked server-side against the database, not
/// against anything the client sent — see architecture.md §3.
///
/// The app still holds them so the UI can disable a control *before* the
/// user taps it and gets a 403, which is a better experience than an error
/// toast, not a security boundary.
@immutable
class Entitlements {
  const Entitlements({
    required this.maxChainLength,
    required this.maxDevices,
    required this.allowedProxyTiers,
    required this.maxProxyTier,
    required this.bandwidthLimitBytes,
    required this.bandwidthUsedBytes,
    required this.bandwidthRemainingBytes,
    required this.isFreeTier,
    required this.source,
    this.bandwidthPeriodStart,
  });

  final int maxChainLength;
  final int maxDevices;
  final List<String> allowedProxyTiers;
  final String maxProxyTier;

  /// 0 means unmetered.
  final int bandwidthLimitBytes;
  final int bandwidthUsedBytes;

  /// Null when unmetered.
  final int? bandwidthRemainingBytes;

  final bool isFreeTier;

  /// `subscription` | `free_plan` | `fallback`.
  ///
  /// `fallback` means the database had no active free plan and the server
  /// degraded to `settings.ENTITLEMENT_FALLBACK` — worth showing, because it
  /// is an operator problem the user cannot fix by upgrading.
  final String source;

  final DateTime? bandwidthPeriodStart;

  bool get isUnmetered => bandwidthLimitBytes <= 0;

  bool get isFallback => source == 'fallback';

  bool allowsTier(String tier) => allowedProxyTiers.contains(tier);

  /// 0.0–1.0, or null when unmetered.
  double? get bandwidthUsedFraction {
    if (isUnmetered) return null;
    return (bandwidthUsedBytes / bandwidthLimitBytes).clamp(0.0, 1.0);
  }

  static const Entitlements unknown = Entitlements(
    maxChainLength: 1,
    maxDevices: 1,
    allowedProxyTiers: <String>['free'],
    maxProxyTier: 'free',
    bandwidthLimitBytes: 0,
    bandwidthUsedBytes: 0,
    bandwidthRemainingBytes: null,
    isFreeTier: true,
    source: 'fallback',
  );

  factory Entitlements.fromJson(Map<String, dynamic> json) => Entitlements(
    maxChainLength: (json['max_chain_length'] as num?)?.toInt() ?? 1,
    maxDevices: (json['max_devices'] as num?)?.toInt() ?? 1,
    allowedProxyTiers:
        (json['allowed_proxy_tiers'] as List<dynamic>?)
            ?.map((e) => '$e')
            .toList(growable: false) ??
        const <String>['free'],
    maxProxyTier: json['max_proxy_tier'] as String? ?? 'free',
    bandwidthLimitBytes: (json['bandwidth_limit_bytes'] as num?)?.toInt() ?? 0,
    bandwidthUsedBytes: (json['bandwidth_used_bytes'] as num?)?.toInt() ?? 0,
    bandwidthRemainingBytes: (json['bandwidth_remaining_bytes'] as num?)
        ?.toInt(),
    isFreeTier: json['is_free_tier'] as bool? ?? true,
    source: json['source'] as String? ?? 'fallback',
    bandwidthPeriodStart: DateTime.tryParse(
      json['bandwidth_period_start'] as String? ?? '',
    ),
  );
}
