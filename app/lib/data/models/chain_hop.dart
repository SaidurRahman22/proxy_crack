import 'package:flutter/foundation.dart';

import 'proxy.dart';

/// One position in an ordered chain. Maps to `CHAINHOP` in graph.md section 3.
///
/// `position` is load-bearing: hop N dials through hop N-1 via sing-box's
/// `detour` field (architecture.md section 1, "Chain semantics").
@immutable
class ChainHop {
  const ChainHop({
    required this.id,
    required this.chainId,
    required this.position,
    required this.proxy,
  });

  /// `CHAINHOP.id`
  final int id;

  /// `CHAINHOP.chain_id`
  final int chainId;

  /// `CHAINHOP.position` -- 0-based, order matters.
  final int position;

  /// Resolved `CHAINHOP.proxy_id`. The API returns the proxy inline so the
  /// list does not need an N+1 round trip.
  final Proxy proxy;

  int get proxyId => proxy.id;

  /// The sing-box outbound tag this hop would be emitted under.
  String get outboundTag => 'hop${position + 1}';

  /// The tag this hop detours through, or null for the first hop.
  String? get detourTag => position == 0 ? null : 'hop$position';

  ChainHop copyWith({int? position, Proxy? proxy}) => ChainHop(
        id: id,
        chainId: chainId,
        position: position ?? this.position,
        proxy: proxy ?? this.proxy,
      );

  /// ⚠️ The API's `ChainHopSerializer` emits **no `chain_id`** (the parent
  /// is implied) and a **null `proxy`** for a constrained hop. This factory
  /// is for a hop that is known to be pinned; the faithful wire shape is
  /// `SavedChainHop`, and `SavedChain.toEditableChain()` is the only place
  /// that converts between them.
  factory ChainHop.fromJson(Map<String, dynamic> json, {int chainId = 0}) =>
      ChainHop(
        id: (json['id'] as num?)?.toInt() ?? 0,
        chainId: (json['chain_id'] as num?)?.toInt() ?? chainId,
        position: (json['position'] as num?)?.toInt() ?? 0,
        proxy: Proxy.fromJson(json['proxy'] as Map<String, dynamic>),
      );

  Map<String, dynamic> toJson() => <String, dynamic>{
        'id': id,
        'chain_id': chainId,
        'position': position,
        'proxy_id': proxyId,
      };

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is ChainHop &&
          other.id == id &&
          other.position == position &&
          other.proxy == proxy);

  @override
  int get hashCode => Object.hash(id, position, proxy);
}
