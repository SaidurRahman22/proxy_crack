import 'package:flutter/foundation.dart';

import '../../data/models/proxy_chain.dart';
import '../../data/models/proxy_credentials.dart';

/// Lifecycle of the tunnel.
///
/// Named [EngineState] rather than `ConnectionState` because Flutter already
/// exports a `ConnectionState` enum from `AsyncSnapshot`.
enum EngineState {
  /// No tunnel. Traffic is going out of the device unproxied.
  disconnected,

  /// Permission prompt, engine start, first hop handshake.
  connecting,

  /// Tunnel is up and carrying traffic.
  connected,

  /// Tearing down. Distinct from [disconnected] so the UI can keep the
  /// control disabled instead of letting the user double-tap into a race.
  disconnecting,

  /// Terminal failure. [EngineStatus.failure] explains why.
  error,
}

/// Why a connection attempt failed. Each value maps to a distinct, actionable
/// message in the UI -- never surface a raw engine string to a user.
enum EngineFailure {
  /// The platform refused, or the user declined, the privilege needed to
  /// capture system traffic. Each platform asks differently; the interface
  /// does not care how.
  permissionDenied,

  /// A hop refused the connection or timed out during handshake.
  hopUnreachable,

  /// The server rejected the credentials for a hop.
  authenticationFailed,

  /// Server-side entitlement said no (chain too long, premium hop on free).
  entitlementRejected,

  /// Device has no usable network.
  networkUnavailable,

  /// Anything the engine could not classify.
  unknown,
}

/// Per-hop health inside a live chain.
///
/// architecture.md section 1: "the chain is only as live as its weakest hop".
/// The engine is the only thing that can observe this once the tunnel is up.
/// Expressed in plain transport terms so every platform can report it.
@immutable
class HopHealth {
  const HopHealth({
    required this.position,
    required this.proxyId,
    required this.isUp,
    this.rttMs,
  });

  final int position;
  final int proxyId;
  final bool isUp;

  /// Measured RTT to this hop, or null if not yet probed.
  final int? rttMs;

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is HopHealth &&
          other.position == position &&
          other.proxyId == proxyId &&
          other.isUp == isUp &&
          other.rttMs == rttMs);

  @override
  int get hashCode => Object.hash(position, proxyId, isUp, rttMs);
}

/// An immutable snapshot of the engine.
@immutable
class EngineStatus {
  const EngineStatus({
    required this.state,
    this.chain,
    this.connectedAt,
    this.failure,
    this.detail,
    this.hopHealth = const <HopHealth>[],
    this.progress,
  });

  final EngineState state;

  /// The chain this status refers to. Null when [EngineState.disconnected].
  final ProxyChain? chain;

  /// When the tunnel came up, for session-duration display.
  final DateTime? connectedAt;

  /// Set only when [state] is [EngineState.error].
  final EngineFailure? failure;

  /// Short, user-facing elaboration. Already localisable copy, not a stack
  /// trace or an engine log line.
  final String? detail;

  /// Health of each hop, ordered by position.
  final List<HopHealth> hopHealth;

  /// 0.0-1.0 while [EngineState.connecting]; null otherwise.
  final double? progress;

  const EngineStatus.idle()
      : state = EngineState.disconnected,
        chain = null,
        connectedAt = null,
        failure = null,
        detail = null,
        hopHealth = const <HopHealth>[],
        progress = null;

  bool get isBusy =>
      state == EngineState.connecting || state == EngineState.disconnecting;

  bool get isConnected => state == EngineState.connected;

  bool get hasError => state == EngineState.error;

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is EngineStatus &&
          other.state == state &&
          other.chain == chain &&
          other.connectedAt == connectedAt &&
          other.failure == failure &&
          other.detail == detail &&
          other.progress == progress &&
          listEquals(other.hopHealth, hopHealth));

  @override
  int get hashCode => Object.hash(
        state,
        chain,
        connectedAt,
        failure,
        detail,
        progress,
        Object.hashAll(hopHealth),
      );
}

/// Live traffic counters. Emitted roughly once a second while connected.
@immutable
class ThroughputStats {
  const ThroughputStats({
    this.bytesDown = 0,
    this.bytesUp = 0,
    this.downBytesPerSecond = 0,
    this.upBytesPerSecond = 0,
    this.sessionDuration = Duration.zero,
    this.measuredLatencyMs,
  });

  /// Session totals, matching `USAGELOG.bytes_down` / `bytes_up`.
  final int bytesDown;
  final int bytesUp;

  /// Instantaneous rates.
  final double downBytesPerSecond;
  final double upBytesPerSecond;

  final Duration sessionDuration;

  /// End-to-end RTT through the whole chain, as actually measured.
  final int? measuredLatencyMs;

  static const ThroughputStats zero = ThroughputStats();

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is ThroughputStats &&
          other.bytesDown == bytesDown &&
          other.bytesUp == bytesUp &&
          other.downBytesPerSecond == downBytesPerSecond &&
          other.upBytesPerSecond == upBytesPerSecond &&
          other.sessionDuration == sessionDuration &&
          other.measuredLatencyMs == measuredLatencyMs);

  @override
  int get hashCode => Object.hash(
        bytesDown,
        bytesUp,
        downBytesPerSecond,
        upBytesPerSecond,
        sessionDuration,
        measuredLatencyMs,
      );
}

/// Thrown by [ProxyEngine.connect] when a tunnel cannot be established.
class EngineException implements Exception {
  const EngineException(this.failure, [this.detail]);

  final EngineFailure failure;
  final String? detail;

  @override
  String toString() => 'EngineException(${failure.name}: ${detail ?? ''})';
}

/// The one and only seam between the Flutter UI and whatever actually moves
/// packets.
///
/// architecture.md section 2: *"Keep every engine call behind
/// `core/routing/`. If the licence forces a swap from sing-box to Xray, that
/// becomes a one-file change instead of a rewrite."*
///
/// **Deliberately platform-agnostic.** Android is phase one, but Windows,
/// Linux and macOS implementations will have to satisfy this same interface
/// unchanged. Nothing here names a `VpnService`, a TUN handle, a WinTun
/// adapter, a utun device or a method channel; everything is expressed as
/// "bring the tunnel up over this chain" plus two observable streams. Nothing
/// above this file may import a platform channel or an engine SDK.
///
/// Expected implementations:
/// * `MockProxyEngine` -- in-memory, used for UI work and tests;
/// * `AndroidProxyEngine` -- `VpnService` plus a sing-box/Xray AAR;
/// * `WindowsProxyEngine` / `LinuxProxyEngine` / `MacProxyEngine` -- the same
///   core bound to WinTun / `/dev/net/tun` / `utun`.
///
/// Each is selected at composition time in `app/proxy_crack_app.dart`; no
/// screen changes.
///
/// Contract:
/// * [connect] is idempotent for an already-connected identical chain.
/// * [connect] and [disconnect] both complete only once the state settles.
/// * [statusStream] and [statsStream] are broadcast and replay the latest
///   value to new listeners, so a screen can rebuild mid-session.
/// * Implementations must tolerate [dispose] being called while connected.
abstract class ProxyEngine {
  /// Latest status, available synchronously for first build.
  EngineStatus get status;

  /// Latest stats, available synchronously for first build.
  ThroughputStats get stats;

  /// Broadcast status updates. Emits the current value on subscribe.
  Stream<EngineStatus> get statusStream;

  /// Broadcast throughput updates. Emits the current value on subscribe.
  Stream<ThroughputStats> get statsStream;

  /// Brings the tunnel up over [chain].
  ///
  /// [credentials] carries one username/password (and Shadowsocks cipher)
  /// per hop, fetched from each hop's `credentials_url` immediately before
  /// this call — see D18. It is **not** part of [chain] on purpose: a chain
  /// is a durable, serialisable, loggable object and credentials are none of
  /// those things. An implementation must hand them to the engine and
  /// forget them; it must never store, cache or log them, and must not keep
  /// them on [status].
  ///
  /// Null is legal and means "connect without credentials" — the mock engine
  /// ignores it entirely, and a hop whose proxy needs none has no entry.
  ///
  /// Throws [EngineException] on failure; the same failure is also published
  /// on [statusStream] as an [EngineState.error] status so late subscribers
  /// see it.
  Future<void> connect(ProxyChain chain, {ChainCredentials? credentials});

  /// Tears the tunnel down. Safe to call when already disconnected.
  Future<void> disconnect();

  /// Releases timers, channels and native handles.
  Future<void> dispose();

  /// Whether this build can route *all* system traffic, or only traffic the
  /// app itself originates. Android answers true; a desktop build without an
  /// installed tunnel driver answers false, and the UI degrades instead of
  /// promising something the platform cannot deliver.
  bool get supportsSystemWideRouting => true;
}
