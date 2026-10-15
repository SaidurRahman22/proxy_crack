import 'package:flutter/foundation.dart';

/// A purchasable (or free) tier. Maps to `PLAN` in graph.md section 3.
///
/// architecture.md section 3 is emphatic that these limits are *enforced
/// server-side*; the copies held here exist only so the UI can explain what a
/// plan buys. Never gate a request on these values alone.
@immutable
class Plan {
  const Plan({
    required this.id,
    required this.name,
    required this.priceCents,
    required this.currency,
    required this.durationDays,
    required this.maxChainLength,
    required this.maxDevices,
    required this.bandwidthLimitBytes,
    required this.isFreeTier,
    this.tagline = '',
  });

  /// `PLAN.id`
  final int id;

  /// `PLAN.name`
  final String name;

  /// `PLAN.price`, in minor units. Stored as an int to keep money off doubles.
  final int priceCents;

  final String currency;

  /// `PLAN.duration_days`
  final int durationDays;

  /// `PLAN.max_chain_length` -- free tier = 1.
  final int maxChainLength;

  /// `PLAN.max_devices`
  final int maxDevices;

  /// `PLAN.bandwidth_limit_bytes` -- 0 means unmetered.
  final int bandwidthLimitBytes;

  /// `PLAN.is_free_tier`
  final bool isFreeTier;

  /// Display-only one-liner. Served from the `CONTENT` table in production.
  final String tagline;

  bool get isUnmetered => bandwidthLimitBytes <= 0;

  String get priceLabel {
    if (isFreeTier || priceCents == 0) return 'Free';
    final major = priceCents ~/ 100;
    final minor = (priceCents % 100).toString().padLeft(2, '0');
    final symbol = currency == 'USD' ? r'$' : '$currency ';
    return '$symbol$major.$minor';
  }

  String get periodLabel => switch (durationDays) {
        0 => '',
        1 => 'per day',
        7 => 'per week',
        >= 28 && <= 31 => 'per month',
        >= 364 && <= 366 => 'per year',
        _ => 'per $durationDays days',
      };

  String get bandwidthLabel {
    if (isUnmetered) return 'Unmetered bandwidth';
    const gb = 1024 * 1024 * 1024;
    if (bandwidthLimitBytes >= gb) {
      final value = bandwidthLimitBytes / gb;
      final text = value == value.roundToDouble()
          ? value.round().toString()
          : value.toStringAsFixed(1);
      return '$text GB per period';
    }
    const mb = 1024 * 1024;
    return '${(bandwidthLimitBytes / mb).round()} MB per period';
  }

  String get chainLabel => maxChainLength <= 1
      ? 'Single hop only'
      : 'Chains up to $maxChainLength hops';

  String get deviceLabel =>
      maxDevices == 1 ? '1 device' : '$maxDevices devices';

  /// Bullet points for the paywall, derived from the model rather than
  /// hand-written per plan, so copy cannot drift from the enforced limits.
  List<String> get entitlements => <String>[
        chainLabel,
        bandwidthLabel,
        deviceLabel,
        isFreeTier ? 'Free-tier proxy pool' : 'Full premium proxy pool',
        if (!isFreeTier) 'Per-hop health checks and failover',
        if (!isFreeTier) 'Priority support',
      ];

  factory Plan.fromJson(Map<String, dynamic> json) => Plan(
        id: json['id'] as int,
        name: json['name'] as String,
        priceCents: json['price_cents'] as int? ?? 0,
        currency: json['currency'] as String? ?? 'USD',
        durationDays: json['duration_days'] as int? ?? 30,
        maxChainLength: json['max_chain_length'] as int? ?? 1,
        maxDevices: json['max_devices'] as int? ?? 1,
        bandwidthLimitBytes: json['bandwidth_limit_bytes'] as int? ?? 0,
        isFreeTier: json['is_free_tier'] as bool? ?? false,
        tagline: json['tagline'] as String? ?? '',
      );

  Map<String, dynamic> toJson() => <String, dynamic>{
        'id': id,
        'name': name,
        'price_cents': priceCents,
        'currency': currency,
        'duration_days': durationDays,
        'max_chain_length': maxChainLength,
        'max_devices': maxDevices,
        'bandwidth_limit_bytes': bandwidthLimitBytes,
        'is_free_tier': isFreeTier,
        'tagline': tagline,
      };

  @override
  bool operator ==(Object other) =>
      identical(this, other) || (other is Plan && other.id == id);

  @override
  int get hashCode => id.hashCode;
}
