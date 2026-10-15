import 'dart:async';
import 'dart:math';

import '../../data/models/proxy_chain.dart';
import 'proxy_engine.dart';

/// In-memory [ProxyEngine] used for UI development and widget tests.
///
/// It deliberately contains **no** VPN code. It simulates the parts of the
/// real engine the UI has to cope with:
///
/// * a connect that takes real time, and takes *longer per hop* -- so the
///   compounding-latency story is visible in the shell before the native
///   engine exists;
/// * a staged progress signal, so the UI can show something other than a
///   bare spinner;
/// * throughput that ramps rather than appearing fully formed;
/// * per-hop health, including a dead hop failing the whole chain.
///
/// Swap this for `SingBoxProxyEngine` at composition time; no screen changes.
class MockProxyEngine implements ProxyEngine {
  MockProxyEngine({Random? random, this.speedMultiplier = 1.0})
      : _random = random ?? Random(7);

  /// Scales every simulated delay. Tests pass a large value to run instantly.
  final double speedMultiplier;

  final Random _random;

  final StreamController<EngineStatus> _statusController =
      StreamController<EngineStatus>.broadcast();
  final StreamController<ThroughputStats> _statsController =
      StreamController<ThroughputStats>.broadcast();

  EngineStatus _status = const EngineStatus.idle();
  ThroughputStats _stats = ThroughputStats.zero;

  Timer? _ticker;
  bool _disposed = false;

  /// The mock pretends to be a full-device tunnel so the UI exercises the
  /// system-wide copy path. A desktop engine without a tunnel driver would
  /// answer false here instead.
  @override
  bool get supportsSystemWideRouting => true;

  @override
  EngineStatus get status => _status;

  @override
  ThroughputStats get stats => _stats;

  @override
  Stream<EngineStatus> get statusStream async* {
    yield _status;
    yield* _statusController.stream;
  }

  @override
  Stream<ThroughputStats> get statsStream async* {
    yield _stats;
    yield* _statsController.stream;
  }

  void _emitStatus(EngineStatus next) {
    if (_disposed) return;
    _status = next;
    if (!_statusController.isClosed) _statusController.add(next);
  }

  void _emitStats(ThroughputStats next) {
    if (_disposed) return;
    _stats = next;
    if (!_statsController.isClosed) _statsController.add(next);
  }

  Duration _scaled(int ms) => Duration(
        milliseconds: (ms / speedMultiplier).round().clamp(0, 60000),
      );

  @override
  Future<void> connect(ProxyChain chain) async {
    if (_status.isConnected && _status.chain == chain) return;
    if (_status.isBusy) return;

    if (chain.isEmpty) {
      const failure = EngineException(
        EngineFailure.hopUnreachable,
        'Add at least one hop before connecting.',
      );
      _emitStatus(
        const EngineStatus(
          state: EngineState.error,
          failure: EngineFailure.hopUnreachable,
          detail: 'Add at least one hop before connecting.',
        ),
      );
      throw failure;
    }

    _ticker?.cancel();
    _emitStats(ThroughputStats.zero);

    final health = <HopHealth>[];
    final total = chain.hops.length;

    _emitStatus(
      EngineStatus(
        state: EngineState.connecting,
        chain: chain,
        progress: 0,
        detail: 'Requesting tunnel permission',
      ),
    );
    await Future<void>.delayed(_scaled(420));
    if (_disposed) return;

    // Each hop is dialled through the previous one, so setup time -- like
    // latency -- compounds. That is the point of the simulation.
    for (var i = 0; i < total; i++) {
      final hop = chain.hops[i];
      _emitStatus(
        EngineStatus(
          state: EngineState.connecting,
          chain: chain,
          progress: (i + 0.5) / (total + 1),
          hopHealth: List<HopHealth>.of(health),
          detail: 'Handshaking hop ${i + 1} of $total '
              '(${hop.proxy.protocol.badge} · ${hop.proxy.countryCode})',
        ),
      );

      await Future<void>.delayed(_scaled(320 + hop.proxy.latencyMs));
      if (_disposed) return;

      if (!hop.proxy.isAlive) {
        _ticker?.cancel();
        final detail =
            'Hop ${i + 1} (${hop.proxy.endpoint}) did not answer. '
            'Replace it in the chain builder and try again.';
        health.add(
          HopHealth(position: i, proxyId: hop.proxy.id, isUp: false),
        );
        _emitStatus(
          EngineStatus(
            state: EngineState.error,
            chain: chain,
            failure: EngineFailure.hopUnreachable,
            detail: detail,
            hopHealth: List<HopHealth>.of(health),
          ),
        );
        throw EngineException(EngineFailure.hopUnreachable, detail);
      }

      health.add(
        HopHealth(
          position: i,
          proxyId: hop.proxy.id,
          isUp: true,
          rttMs: hop.proxy.latencyMs + _random.nextInt(25),
        ),
      );
    }

    _emitStatus(
      EngineStatus(
        state: EngineState.connecting,
        chain: chain,
        progress: 0.95,
        hopHealth: List<HopHealth>.of(health),
        detail: 'Routing device traffic into the tunnel',
      ),
    );
    await Future<void>.delayed(_scaled(380));
    if (_disposed) return;

    final connectedAt = DateTime.now();
    _emitStatus(
      EngineStatus(
        state: EngineState.connected,
        chain: chain,
        connectedAt: connectedAt,
        hopHealth: List<HopHealth>.of(health),
      ),
    );
    _startTicker(chain, connectedAt);
  }

  void _startTicker(ProxyChain chain, DateTime connectedAt) {
    var down = 0;
    var up = 0;
    _ticker = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (_disposed || !_status.isConnected) {
        timer.cancel();
        return;
      }
      // A chained route throttles throughput; more hops, less headroom.
      final ceiling = 2600000 / (1 + chain.length * 0.55);
      final downRate = ceiling * (0.35 + _random.nextDouble() * 0.65);
      final upRate = downRate * (0.08 + _random.nextDouble() * 0.12);
      down += downRate.round();
      up += upRate.round();

      final jitter = _random.nextInt(60) - 20;
      _emitStats(
        ThroughputStats(
          bytesDown: down,
          bytesUp: up,
          downBytesPerSecond: downRate,
          upBytesPerSecond: upRate,
          sessionDuration: DateTime.now().difference(connectedAt),
          measuredLatencyMs:
              (chain.estimatedLatencyMs + jitter).clamp(20, 5000),
        ),
      );
    });
  }

  @override
  Future<void> disconnect() async {
    if (_status.state == EngineState.disconnected) return;
    _ticker?.cancel();
    _ticker = null;

    _emitStatus(
      EngineStatus(
        state: EngineState.disconnecting,
        chain: _status.chain,
        connectedAt: _status.connectedAt,
        hopHealth: _status.hopHealth,
        detail: 'Closing the tunnel',
      ),
    );
    await Future<void>.delayed(_scaled(460));
    if (_disposed) return;

    _emitStats(ThroughputStats.zero);
    _emitStatus(const EngineStatus.idle());
  }

  @override
  Future<void> dispose() async {
    _disposed = true;
    _ticker?.cancel();
    _ticker = null;
    await _statusController.close();
    await _statsController.close();
  }
}
