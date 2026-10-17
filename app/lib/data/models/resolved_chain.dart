import 'package:flutter/foundation.dart';

import 'proxy.dart';

/// One slot of a resolved chain, as `ResolvedHopSerializer` sends it.
///
/// **No credentials.** That is D18: a resolve that inlined them would hand
/// over three sets of paid credentials in one request, outside the 60/hour
/// throttle and outside the per-disclosure audit line. The client pays three
/// extra round trips instead, one per [credentialsUrl].
@immutable
class ResolvedHop {
  const ResolvedHop({
    required this.position,
    required this.hopId,
    required this.fixed,
    required this.proxy,
    required this.credentialsUrl,
    this.constraints = const <String, dynamic>{},
    this.candidateCount = 0,
    this.failoverCapable = false,
  });

  /// 0-based. Hop 0 is the first hop the client dials.
  final int position;

  /// The `ChainHop` row this slot came from.
  final int hopId;

  /// True when the hop was pinned to a proxy; false when the resolver chose.
  final bool fixed;

  /// The concrete proxy that will carry this hop *right now*. A later
  /// resolve may legitimately return a different one.
  final Proxy proxy;

  /// Absolute URL of `GET /api/proxies/{id}/`.
  final String credentialsUrl;

  /// What a constrained hop asked for. Empty for a fixed hop.
  final Map<String, dynamic> constraints;

  /// How many proxies satisfied this slot. `1` means no alternative if it
  /// dies mid-session.
  final int candidateCount;

  /// False when this hop has no failover candidate.
  final bool failoverCapable;

  factory ResolvedHop.fromJson(Map<String, dynamic> json) => ResolvedHop(
    position: (json['position'] as num?)?.toInt() ?? 0,
    hopId: (json['hop_id'] as num?)?.toInt() ?? 0,
    fixed: json['fixed'] as bool? ?? true,
    proxy: Proxy.fromJson(
      (json['proxy'] as Map<String, dynamic>?) ?? const <String, dynamic>{},
    ),
    credentialsUrl: json['credentials_url'] as String? ?? '',
    constraints:
        (json['constraints'] as Map<String, dynamic>?) ??
        const <String, dynamic>{},
    candidateCount: (json['candidate_count'] as num?)?.toInt() ?? 0,
    failoverCapable: json['failover_capable'] as bool? ?? false,
  );
}

/// The answer to `POST /api/chains/{id}/resolve/`.
///
/// A point-in-time decision, not a representation — which is exactly why the
/// backend made it a POST. It must not be cached, and the app re-resolves on
/// every connect and on every failover.
@immutable
class ResolvedChain {
  const ResolvedChain({
    required this.chainId,
    required this.chainName,
    required this.resolvedAt,
    required this.hops,
  });

  final int chainId;
  final String chainName;
  final DateTime resolvedAt;
  final List<ResolvedHop> hops;

  int get length => hops.length;

  /// Hops with exactly one candidate — if one of these dies, the session
  /// drops rather than failing over.
  List<ResolvedHop> get singleCandidateHops =>
      hops.where((h) => !h.failoverCapable).toList(growable: false);

  /// False when any hop is HTTP: an HTTP proxy is TCP-only, so the whole
  /// chain is (architecture.md §1, P7).
  bool get carriesUdp =>
      hops.isNotEmpty && hops.every((h) => h.proxy.protocol.carriesUdp);

  factory ResolvedChain.fromJson(Map<String, dynamic> json) {
    final chain = (json['chain'] as Map<String, dynamic>?) ??
        const <String, dynamic>{};
    return ResolvedChain(
      chainId: (chain['id'] as num?)?.toInt() ?? 0,
      chainName: chain['name'] as String? ?? '',
      resolvedAt:
          DateTime.tryParse(json['resolved_at'] as String? ?? '') ??
              DateTime.now(),
      hops: (json['hops'] as List<dynamic>? ?? const <dynamic>[])
          .whereType<Map<String, dynamic>>()
          .map(ResolvedHop.fromJson)
          .toList(growable: false),
    );
  }
}
