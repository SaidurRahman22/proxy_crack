import 'package:flutter/foundation.dart';

import 'chain_hop.dart';
import 'proxy.dart';

/// How much extra latency the user should brace for.
///
/// architecture.md section 1 and section 6 both make the same point: a 3-hop
/// chain is routinely 400-900 ms, and "users read that as 'the app is slow'
/// unless the UI sets expectations". This enum is what the UI branches on.
enum LatencyExpectation {
  /// No chain configured.
  none,

  /// Single hop -- behaves like an ordinary VPN.
  direct,

  /// Two hops -- perceptible but usable.
  noticeable,

  /// Three or more hops -- page loads will visibly lag. Warn loudly.
  severe,
}

/// A saved, ordered chain of proxies. Maps to `PROXYCHAIN` in graph.md
/// section 3, with its `CHAINHOP` children resolved inline.
@immutable
class ProxyChain {
  const ProxyChain({
    required this.id,
    required this.userId,
    required this.name,
    required this.createdAt,
    required this.hops,
  });

  /// `PROXYCHAIN.id`
  final int id;

  /// `PROXYCHAIN.user_id`
  final int userId;

  /// `PROXYCHAIN.name`
  final String name;

  /// `PROXYCHAIN.created_at`
  final DateTime createdAt;

  /// Ordered `CHAINHOP` rows. Index equals `ChainHop.position`.
  final List<ChainHop> hops;

  static const int emptyId = -1;

  bool get isEmpty => hops.isEmpty;

  int get length => hops.length;

  List<Proxy> get proxies =>
      hops.map((h) => h.proxy).toList(growable: false);

  /// True when every hop passed its last health check. architecture.md:
  /// "the chain is only as live as its weakest hop".
  bool get isHealthy => hops.isNotEmpty && hops.every((h) => h.proxy.isAlive);

  List<ChainHop> get deadHops =>
      hops.where((h) => !h.proxy.isAlive).toList(growable: false);

  bool get requiresPremium => hops.any((h) => h.proxy.isPremium);

  /// Expected round-trip, in ms: every hop contributes a full RTT, plus a
  /// per-hop connection-setup cost for the CONNECT/handshake exchange.
  int get estimatedLatencyMs {
    if (hops.isEmpty) return 0;
    final sum = hops.fold<int>(0, (a, h) => a + h.proxy.latencyMs);
    const setupPerHop = 35;
    return sum + setupPerHop * hops.length;
  }

  /// Pessimistic end of the range. Real-world chains spread wide; quoting a
  /// band is more honest than quoting a single number.
  int get worstCaseLatencyMs => (estimatedLatencyMs * 1.8).round();

  LatencyExpectation get latencyExpectation => switch (hops.length) {
        0 => LatencyExpectation.none,
        1 => LatencyExpectation.direct,
        2 => LatencyExpectation.noticeable,
        _ => LatencyExpectation.severe,
      };

  /// Country codes in order, e.g. `NL -> DE -> SG`.
  String get routeSummary =>
      hops.map((h) => h.proxy.countryCode).join('  →  ');

  ProxyChain copyWith({String? name, List<ChainHop>? hops}) => ProxyChain(
        id: id,
        userId: userId,
        name: name ?? this.name,
        createdAt: createdAt,
        hops: hops ?? this.hops,
      );

  /// Returns a copy with [proxy] appended as the last hop.
  ProxyChain withHopAdded(Proxy proxy) {
    final next = List<ChainHop>.of(hops)
      ..add(
        ChainHop(
          id: DateTime.now().microsecondsSinceEpoch,
          chainId: id,
          position: hops.length,
          proxy: proxy,
        ),
      );
    return copyWith(hops: _reindex(next));
  }

  ProxyChain withHopRemovedAt(int index) {
    if (index < 0 || index >= hops.length) return this;
    final next = List<ChainHop>.of(hops)..removeAt(index);
    return copyWith(hops: _reindex(next));
  }

  /// Reorder a hop.
  ///
  /// [newIndex] is the *final* destination index, already adjusted for the
  /// removal -- i.e. the semantics of `ReorderableListView.onReorderItem`,
  /// not the older `onReorder`.
  ProxyChain withHopMoved(int oldIndex, int newIndex) {
    if (oldIndex < 0 || oldIndex >= hops.length) return this;
    if (oldIndex == newIndex) return this;
    final next = List<ChainHop>.of(hops);
    final moved = next.removeAt(oldIndex);
    next.insert(newIndex.clamp(0, next.length), moved);
    return copyWith(hops: _reindex(next));
  }

  static List<ChainHop> _reindex(List<ChainHop> list) => List<ChainHop>.generate(
        list.length,
        (i) => list[i].copyWith(position: i),
        growable: false,
      );

  factory ProxyChain.empty({String name = 'Untitled chain'}) => ProxyChain(
        id: emptyId,
        userId: 0,
        name: name,
        createdAt: DateTime.fromMillisecondsSinceEpoch(0),
        hops: const <ChainHop>[],
      );

  factory ProxyChain.fromJson(Map<String, dynamic> json) => ProxyChain(
        id: json['id'] as int,
        userId: json['user_id'] as int? ?? 0,
        name: json['name'] as String? ?? 'Untitled chain',
        createdAt: DateTime.tryParse(json['created_at'] as String? ?? '') ??
            DateTime.fromMillisecondsSinceEpoch(0),
        hops: ((json['hops'] as List<dynamic>?) ?? const <dynamic>[])
            .map((e) => ChainHop.fromJson(e as Map<String, dynamic>))
            .toList(growable: false),
      );

  Map<String, dynamic> toJson() => <String, dynamic>{
        'id': id,
        'user_id': userId,
        'name': name,
        'created_at': createdAt.toIso8601String(),
        'hops': hops.map((h) => h.toJson()).toList(growable: false),
      };

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is ProxyChain &&
          other.id == id &&
          other.name == name &&
          listEquals(other.hops, hops));

  @override
  int get hashCode => Object.hash(id, name, Object.hashAll(hops));
}
