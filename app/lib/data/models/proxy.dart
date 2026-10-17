import 'package:flutter/foundation.dart';

import 'country_code.dart';

/// Wire protocol of a single proxy hop.
///
/// Mirrors `PROXY.protocol` in `Project/graph.md` section 3: `ss|socks5|http`.
enum ProxyProtocol {
  shadowsocks('ss', 'SS', 'Shadowsocks'),
  socks5('socks5', 'SOCKS5', 'SOCKS5'),
  http('http', 'HTTP', 'HTTP CONNECT');

  const ProxyProtocol(this.wire, this.badge, this.label);

  /// Value as stored by the backend and emitted in an Xray outbound.
  final String wire;

  /// Short form for a badge chip.
  final String badge;

  /// Human-readable name.
  final String label;

  static ProxyProtocol fromWire(String value) =>
      ProxyProtocol.values.firstWhere(
        (p) => p.wire == value,
        orElse: () => ProxyProtocol.http,
      );

  /// An HTTP hop is TCP-only — it cannot carry UDP, so any chain containing
  /// one has no QUIC, WebRTC, VoIP or most games (architecture.md §1, P7).
  bool get carriesUdp => this != ProxyProtocol.http;
}

/// Entitlement tier a proxy belongs to. `PROXY.tier` = `free|premium`.
enum ProxyTier {
  free('free', 'Free'),
  premium('premium', 'Premium');

  const ProxyTier(this.wire, this.label);

  final String wire;
  final String label;

  static ProxyTier fromWire(String value) => ProxyTier.values.firstWhere(
    (t) => t.wire == value,
    orElse: () => ProxyTier.free,
  );
}

/// A single proxy server. Maps to the `PROXY` entity in graph.md section 3.
///
/// `username` / `password` are deliberately *absent* from this model.
/// `ProxyListSerializer` never emits them, and D18 fetches them one proxy at
/// a time at connect time into [ProxyCredentials], which is never stored.
@immutable
class Proxy {
  const Proxy({
    required this.id,
    required this.protocol,
    required this.host,
    required this.port,
    required this.country,
    required this.countryCode,
    required this.city,
    required this.tier,
    required this.isAlive,
    required this.latencyMs,
    required this.lastChecked,
    this.successRate,
    this.probeCount = 0,
    this.consecutiveFailures = 0,
    this.latencyP50Ms,
    this.latencyP95Ms,
    this.qualityScore = unprobedQualityScore,
    this.requiresCredentials = false,
    this.uploadBatchId,
  });

  /// Mirrors `PROXY_UNPROBED_SCORE` / D15: a proxy nobody has measured sits
  /// below anything proven good and above anything proven bad.
  static const double unprobedQualityScore = 50;

  /// `PROXY.id`
  final int id;

  /// `PROXY.protocol`
  final ProxyProtocol protocol;

  /// `PROXY.host`
  final String host;

  /// `PROXY.port`
  final int port;

  /// `PROXY.country` — free text; may be empty.
  final String country;

  /// Derived client-side from [country]; there is no such column on the
  /// wire. See [CountryCode].
  final String countryCode;

  /// `PROXY.city`
  final String city;

  /// `PROXY.tier`
  final ProxyTier tier;

  // --- R1 health ---------------------------------------------------------

  /// `PROXY.is_alive` — false only after `PROXY_DEAD_AFTER_FAILURES`
  /// consecutive failures (D16). One bad probe is noise, not death.
  final bool isAlive;

  /// `PROXY.latency_ms` — the most recent *successful* probe. Zero when the
  /// server sent null, which means "never successfully probed".
  final int latencyMs;

  /// `PROXY.last_checked`
  final DateTime lastChecked;

  /// `PROXY.success_rate`, 0.0–1.0 over the rolling window. Null until the
  /// proxy has been probed at least once — null is **not** zero, and the UI
  /// must not render it as a failure.
  final double? successRate;

  /// `PROXY.probe_count` — the sample [successRate] is computed from. This,
  /// not a null latency, is how "unprobed" is defined (P15).
  final int probeCount;

  /// `PROXY.consecutive_failures`
  final int consecutiveFailures;

  /// `PROXY.latency_p50_ms`
  final int? latencyP50Ms;

  /// `PROXY.latency_p95_ms` — the field to judge a hop on. A good mean with
  /// a terrible tail is a bad hop and the mean hides it.
  final int? latencyP95Ms;

  /// `PROXY.quality_score`, 0–100. Reliability dominant, latency secondary
  /// (D13). This is the server's default ordering, so the list arrives
  /// best-first rather than fastest-first.
  final double qualityScore;

  /// True when the proxy has stored credentials that a connect will have to
  /// fetch. Lets the connect flow count its round trips before making them.
  final bool requiresCredentials;

  /// `PROXY.upload_batch_id`
  final int? uploadBatchId;

  String get endpoint => '$host:$port';

  String get location {
    if (city.isEmpty && country.isEmpty) return host;
    if (city.isEmpty) return country;
    if (country.isEmpty) return city;
    return '$city, $country';
  }

  bool get isPremium => tier == ProxyTier.premium;

  /// A short identifier for a snackbar or a route summary.
  ///
  /// Falls back to the host when the operator recorded no country, which is
  /// the case for the one real proxy in the dev database — `"Added -- as
  /// hop 1"` tells the user nothing at all.
  String get shortLabel =>
      countryCode == CountryCode.unknown ? host : countryCode;

  /// True when no probe has ever run against this proxy.
  ///
  /// Keyed on [probeCount], never on a null latency: a proxy that failed
  /// fifty probes also has a null p95, and treating that as "nothing known
  /// yet" would promote the worst proxies in the inventory (P15).
  bool get isUnprobed => probeCount == 0;

  /// Latency to rank and display on, preferring the p95 when there is one.
  int? get effectiveLatencyMs {
    if (latencyP95Ms != null) return latencyP95Ms;
    return latencyMs > 0 ? latencyMs : null;
  }

  /// `success_rate` as a percentage, or null when unprobed.
  int? get successPercent =>
      successRate == null ? null : (successRate! * 100).round();

  /// Coarse latency band, used to pick a colour without hard-coding one.
  LatencyGrade get latencyGrade {
    if (!isAlive) return LatencyGrade.dead;
    final latency = effectiveLatencyMs;
    if (latency == null) return LatencyGrade.unknown;
    if (latency <= 90) return LatencyGrade.fast;
    if (latency <= 200) return LatencyGrade.moderate;
    return LatencyGrade.slow;
  }

  Proxy copyWith({
    bool? isAlive,
    int? latencyMs,
    DateTime? lastChecked,
    double? qualityScore,
  }) {
    return Proxy(
      id: id,
      protocol: protocol,
      host: host,
      port: port,
      country: country,
      countryCode: countryCode,
      city: city,
      tier: tier,
      isAlive: isAlive ?? this.isAlive,
      latencyMs: latencyMs ?? this.latencyMs,
      lastChecked: lastChecked ?? this.lastChecked,
      successRate: successRate,
      probeCount: probeCount,
      consecutiveFailures: consecutiveFailures,
      latencyP50Ms: latencyP50Ms,
      latencyP95Ms: latencyP95Ms,
      qualityScore: qualityScore ?? this.qualityScore,
      requiresCredentials: requiresCredentials,
      uploadBatchId: uploadBatchId,
    );
  }

  /// Parses both `ProxyListSerializer` (the browser) and
  /// `ProxySummarySerializer` (embedded in a chain or a resolution). The
  /// summary omits several fields, so every health field tolerates absence.
  factory Proxy.fromJson(Map<String, dynamic> json) {
    final country = json['country'] as String? ?? '';
    return Proxy(
      id: (json['id'] as num).toInt(),
      protocol: ProxyProtocol.fromWire(json['protocol'] as String? ?? 'http'),
      host: json['host'] as String? ?? '',
      port: (json['port'] as num?)?.toInt() ?? 0,
      country: country,
      countryCode: CountryCode.fromCountry(country),
      city: json['city'] as String? ?? '',
      tier: ProxyTier.fromWire(json['tier'] as String? ?? 'free'),
      isAlive: json['is_alive'] as bool? ?? false,
      latencyMs: (json['latency_ms'] as num?)?.toInt() ?? 0,
      lastChecked:
          DateTime.tryParse(json['last_checked'] as String? ?? '') ??
          DateTime.fromMillisecondsSinceEpoch(0),
      successRate: (json['success_rate'] as num?)?.toDouble(),
      probeCount: (json['probe_count'] as num?)?.toInt() ?? 0,
      consecutiveFailures: (json['consecutive_failures'] as num?)?.toInt() ?? 0,
      latencyP50Ms: (json['latency_p50_ms'] as num?)?.toInt(),
      latencyP95Ms: (json['latency_p95_ms'] as num?)?.toInt(),
      qualityScore:
          (json['quality_score'] as num?)?.toDouble() ?? unprobedQualityScore,
      requiresCredentials: json['requires_credentials'] as bool? ?? false,
      uploadBatchId: (json['upload_batch_id'] as num?)?.toInt(),
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
    'id': id,
    'protocol': protocol.wire,
    'host': host,
    'port': port,
    'country': country,
    'city': city,
    'tier': tier.wire,
    'is_alive': isAlive,
    'latency_ms': latencyMs,
    'last_checked': lastChecked.toIso8601String(),
    'success_rate': successRate,
    'probe_count': probeCount,
    'consecutive_failures': consecutiveFailures,
    'latency_p50_ms': latencyP50Ms,
    'latency_p95_ms': latencyP95Ms,
    'quality_score': qualityScore,
    'requires_credentials': requiresCredentials,
    'upload_batch_id': uploadBatchId,
  };

  @override
  bool operator ==(Object other) =>
      identical(this, other) || (other is Proxy && other.id == id);

  @override
  int get hashCode => id.hashCode;

  @override
  String toString() => 'Proxy($id, ${protocol.wire}, $endpoint)';
}

enum LatencyGrade { fast, moderate, slow, dead, unknown }
