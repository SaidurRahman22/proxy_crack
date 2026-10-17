import 'package:flutter/foundation.dart';

import 'chain_hop.dart';
import 'proxy.dart';
import 'proxy_chain.dart';

/// A hop exactly as `ChainHopSerializer` sends it.
///
/// **`proxy` is nullable, and that is not a defect.** A *constrained* hop —
/// "any German SOCKS5 proxy under 200 ms p95" — has no concrete proxy until
/// `POST /api/chains/{id}/resolve/` runs. The UI model [ChainHop] keeps a
/// non-null proxy because every screen that renders a hop needs a host, a
/// protocol and a latency to show; this DTO is the faithful wire shape, and
/// [SavedChain.toEditableChain] is the one place the two meet.
@immutable
class SavedChainHop {
  const SavedChainHop({
    required this.id,
    required this.position,
    required this.isConstrained,
    this.proxy,
    this.protocol = '',
    this.country = '',
    this.tier = '',
    this.maxLatencyMs,
    this.minSuccessRate,
    this.allowUnprobed = false,
  });

  final int id;
  final int position;

  /// True when the hop is a slot the resolver fills, not a pinned proxy.
  final bool isConstrained;

  /// Null for a constrained hop.
  final Proxy? proxy;

  final String protocol;
  final String country;
  final String tier;
  final int? maxLatencyMs;
  final double? minSuccessRate;

  /// Opt-in to accepting never-probed proxies against a latency or success
  /// floor. Off by default — those fields ask for a guarantee and an
  /// unprobed proxy cannot make one (P15).
  final bool allowUnprobed;

  factory SavedChainHop.fromJson(Map<String, dynamic> json) {
    final proxyJson = json['proxy'] as Map<String, dynamic>?;
    return SavedChainHop(
      id: (json['id'] as num?)?.toInt() ?? 0,
      position: (json['position'] as num?)?.toInt() ?? 0,
      isConstrained: json['is_constrained'] as bool? ?? proxyJson == null,
      proxy: proxyJson == null ? null : Proxy.fromJson(proxyJson),
      protocol: json['protocol'] as String? ?? '',
      country: json['country'] as String? ?? '',
      tier: json['tier'] as String? ?? '',
      maxLatencyMs: (json['max_latency_ms'] as num?)?.toInt(),
      minSuccessRate: (json['min_success_rate'] as num?)?.toDouble(),
      allowUnprobed: json['allow_unprobed'] as bool? ?? false,
    );
  }
}

/// A saved chain as `ProxyChainSerializer` sends it.
@immutable
class SavedChain {
  const SavedChain({
    required this.id,
    required this.name,
    required this.hops,
    required this.length,
    required this.hasConstrainedHops,
    this.createdAt,
    this.updatedAt,
  });

  final int id;
  final String name;
  final List<SavedChainHop> hops;
  final int length;

  /// True when at least one hop is a slot rather than a pinned proxy.
  ///
  /// The chain builder in this app only ever writes pinned hops, so a chain
  /// with constrained hops was created elsewhere (admin console, direct
  /// API). The builder says so rather than rendering a half-populated row.
  final bool hasConstrainedHops;

  final DateTime? createdAt;
  final DateTime? updatedAt;

  /// Converts to the UI's editable chain, or null when a hop has no proxy.
  ///
  /// Null rather than a placeholder on purpose: silently dropping an
  /// unresolved hop would show the user a two-hop route for a three-hop
  /// chain, and inventing a fake proxy to fill the gap would put a host that
  /// does not exist in front of them.
  ProxyChain? toEditableChain() {
    if (hasConstrainedHops || hops.any((h) => h.proxy == null)) return null;
    return ProxyChain(
      id: id,
      userId: 0,
      name: name,
      createdAt: createdAt ?? DateTime.fromMillisecondsSinceEpoch(0),
      hops: <ChainHop>[
        for (final hop in hops)
          ChainHop(
            id: hop.id,
            chainId: id,
            position: hop.position,
            proxy: hop.proxy!,
          ),
      ]..sort((a, b) => a.position.compareTo(b.position)),
    );
  }

  factory SavedChain.fromJson(Map<String, dynamic> json) {
    final hops = (json['hops'] as List<dynamic>? ?? const <dynamic>[])
        .whereType<Map<String, dynamic>>()
        .map(SavedChainHop.fromJson)
        .toList(growable: false);
    return SavedChain(
      id: (json['id'] as num?)?.toInt() ?? 0,
      name: json['name'] as String? ?? 'Untitled chain',
      hops: hops,
      length: (json['length'] as num?)?.toInt() ?? hops.length,
      hasConstrainedHops:
          json['has_constrained_hops'] as bool? ??
          hops.any((h) => h.isConstrained),
      createdAt: DateTime.tryParse(json['created_at'] as String? ?? ''),
      updatedAt: DateTime.tryParse(json['updated_at'] as String? ?? ''),
    );
  }
}
