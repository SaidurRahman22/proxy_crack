import 'package:flutter/foundation.dart';

/// Wire protocol of a single proxy hop.
///
/// Mirrors `PROXY.protocol` in `Project/graph.md` section 3: `ss|socks5|http`.
enum ProxyProtocol {
  shadowsocks('ss', 'SS', 'Shadowsocks'),
  socks5('socks5', 'SOCKS5', 'SOCKS5'),
  http('http', 'HTTP', 'HTTP CONNECT');

  const ProxyProtocol(this.wire, this.badge, this.label);

  /// Value as stored by the backend and emitted in a sing-box outbound.
  final String wire;

  /// Short form for a badge chip.
  final String badge;

  /// Human-readable name.
  final String label;

  static ProxyProtocol fromWire(String value) => ProxyProtocol.values.firstWhere(
        (p) => p.wire == value,
        orElse: () => ProxyProtocol.http,
      );
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
/// `username` / `password` are deliberately *absent* from this client model.
/// graph.md marks them "encrypted" and architecture.md section 3 is explicit
/// that credentials must never be shipped to the client in bulk -- the engine
/// receives them server-side when a chain is instantiated.
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
    this.uploadBatchId,
  });

  /// `PROXY.id`
  final int id;

  /// `PROXY.protocol`
  final ProxyProtocol protocol;

  /// `PROXY.host`
  final String host;

  /// `PROXY.port`
  final int port;

  /// `PROXY.country`
  final String country;

  /// ISO 3166-1 alpha-2. Not in graph.md; added client-side so the list can
  /// show a country chip instead of a flag emoji (emoji-as-icon is banned by
  /// the design system). The backend can derive it from `country`.
  final String countryCode;

  /// `PROXY.city`
  final String city;

  /// `PROXY.tier`
  final ProxyTier tier;

  /// `PROXY.is_alive` -- last health-check verdict.
  final bool isAlive;

  /// `PROXY.latency_ms` -- single-hop RTT, not chain latency.
  final int latencyMs;

  /// `PROXY.last_checked`
  final DateTime lastChecked;

  /// `PROXY.upload_batch_id`
  final int? uploadBatchId;

  String get endpoint => '$host:$port';

  String get location => city.isEmpty ? country : '$city, $country';

  bool get isPremium => tier == ProxyTier.premium;

  /// Coarse latency band, used to pick a colour without hard-coding one here.
  LatencyGrade get latencyGrade {
    if (!isAlive) return LatencyGrade.dead;
    if (latencyMs <= 90) return LatencyGrade.fast;
    if (latencyMs <= 200) return LatencyGrade.moderate;
    return LatencyGrade.slow;
  }

  Proxy copyWith({bool? isAlive, int? latencyMs, DateTime? lastChecked}) {
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
      uploadBatchId: uploadBatchId,
    );
  }

  factory Proxy.fromJson(Map<String, dynamic> json) {
    return Proxy(
      id: json['id'] as int,
      protocol: ProxyProtocol.fromWire(json['protocol'] as String),
      host: json['host'] as String,
      port: json['port'] as int,
      country: json['country'] as String? ?? '',
      countryCode: json['country_code'] as String? ?? '',
      city: json['city'] as String? ?? '',
      tier: ProxyTier.fromWire(json['tier'] as String? ?? 'free'),
      isAlive: json['is_alive'] as bool? ?? false,
      latencyMs: json['latency_ms'] as int? ?? 0,
      lastChecked:
          DateTime.tryParse(json['last_checked'] as String? ?? '') ??
              DateTime.fromMillisecondsSinceEpoch(0),
      uploadBatchId: json['upload_batch_id'] as int?,
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'id': id,
        'protocol': protocol.wire,
        'host': host,
        'port': port,
        'country': country,
        'country_code': countryCode,
        'city': city,
        'tier': tier.wire,
        'is_alive': isAlive,
        'latency_ms': latencyMs,
        'last_checked': lastChecked.toIso8601String(),
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

enum LatencyGrade { fast, moderate, slow, dead }
