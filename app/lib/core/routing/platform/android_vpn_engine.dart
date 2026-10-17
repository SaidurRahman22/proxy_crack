import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import '../../../data/models/proxy_chain.dart';
import '../../../data/models/proxy_credentials.dart';
import '../proxy_engine.dart';
import '../xray_config_builder.dart';

/// The Android implementation of [ProxyEngine]: `VpnService` + libXray.
///
/// This is layer 3 of engine-decision.md §4.1 — *transport only*. It knows
/// how to reach the Kotlin service (two platform channels) and nothing about
/// what a tunnel is. All chain semantics live in [XrayConfigBuilder] above
/// it; all `VpnService`, TUN and engine lifecycle lives in
/// `VpnConnectionService.kt` below it.
///
/// **[ProxyEngine] was not changed to make this fit.** The one thing that
/// needed care was [connect] completing *only once the state settles*: the
/// `MethodChannel` call returns as soon as the service has been asked to
/// start, so the future is completed from the status stream instead.
class AndroidVpnEngine implements ProxyEngine {
  AndroidVpnEngine({
    this._configBuilder = const XrayConfigBuilder(),
    MethodChannel? methodChannel,
    EventChannel? statusChannel,
    EventChannel? statsChannel,
  })  : _methods = methodChannel ?? const MethodChannel(methodChannelName),
        _statusEvents = statusChannel ?? const EventChannel(statusChannelName),
        _statsEvents = statsChannel ?? const EventChannel(statsChannelName) {
    _attach();
  }

  static const String methodChannelName = 'com.nenobot.proxy_crack/vpn';
  static const String statusChannelName = 'com.nenobot.proxy_crack/vpn_status';
  static const String statsChannelName = 'com.nenobot.proxy_crack/vpn_stats';

  /// A three-hop chain dials three handshakes in series, each through the
  /// last. Generous, but not unbounded: a hung dial must not leave the UI
  /// spinning forever.
  static const Duration connectTimeout = Duration(seconds: 60);
  static const Duration disconnectTimeout = Duration(seconds: 15);

  final XrayConfigBuilder _configBuilder;
  final MethodChannel _methods;
  final EventChannel _statusEvents;
  final EventChannel _statsEvents;

  final StreamController<EngineStatus> _statusController =
      StreamController<EngineStatus>.broadcast();
  final StreamController<ThroughputStats> _statsController =
      StreamController<ThroughputStats>.broadcast();

  StreamSubscription<dynamic>? _statusSub;
  StreamSubscription<dynamic>? _statsSub;

  EngineStatus _status = const EngineStatus.idle();
  ThroughputStats _stats = ThroughputStats.zero;

  /// The chain the service is carrying. The native side deals in config
  /// JSON and has no idea what a [ProxyChain] is, so the Dart side keeps it
  /// and reattaches it to every status it publishes.
  ProxyChain? _chain;

  Completer<void>? _settling;
  bool _disposed = false;

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

  /// Subscribes immediately rather than on first UI listen.
  ///
  /// engine-decision.md §4.2 rule 4: state is authoritative in the service
  /// and replayed on subscribe. The service outlives the Activity, so the
  /// app can be launched into an already-connected tunnel; if nothing were
  /// listening until a screen built, the first thing the user saw would be
  /// "Disconnected" over a live VPN.
  void _attach() {
    _statusSub = _statusEvents.receiveBroadcastStream().listen(
      (dynamic event) {
        if (event is Map) _onNativeStatus(event);
      },
      onError: (Object error) {
        _publish(
          EngineStatus(
            state: EngineState.error,
            chain: _chain,
            failure: EngineFailure.unknown,
            detail: 'Lost contact with the tunnel service.',
          ),
        );
      },
    );

    _statsSub = _statsEvents.receiveBroadcastStream().listen(
      (dynamic event) {
        if (event is Map) _onNativeStats(event);
      },
      onError: (Object _) {
        // Throughput is cosmetic; losing it must not change tunnel state.
      },
    );

    // Cold start: ask once, in case the service was already running before
    // this Dart isolate existed.
    unawaited(_syncStatus());
  }

  Future<void> _syncStatus() async {
    try {
      final result = await _methods.invokeMapMethod<String, Object?>('status');
      if (result != null) _onNativeStatus(result);
    } on PlatformException {
      // Nothing is running; the idle default already says so.
    } on MissingPluginException {
      // Host without the native side (desktop debug build of the Flutter
      // tree). The factory should not have selected this engine, but a
      // crash here would be a poor way to find out.
    }
  }

  void _onNativeStatus(Map<dynamic, dynamic> event) {
    if (_disposed) return;

    final stateName = event['state'] as String? ?? 'disconnected';
    final state = _stateFromWire(stateName);
    final failureName = event['failure'] as String?;
    final connectedAtMillis = (event['connectedAtMillis'] as num?)?.toInt();

    if (state == EngineState.disconnected) _chain = null;

    final next = EngineStatus(
      state: state,
      chain: state == EngineState.disconnected ? null : _chain,
      connectedAt: connectedAtMillis == null
          ? null
          : DateTime.fromMillisecondsSinceEpoch(connectedAtMillis),
      failure: failureName == null ? null : _failureFromWire(failureName),
      detail: event['detail'] as String?,
      progress: (event['progress'] as num?)?.toDouble(),
      // Per-hop attribution is deliberately absent. With `dialerProxy` a
      // failure at hop 1 surfaces as a generic dial failure at the exit
      // hop (§9.7), so the engine cannot honestly say which hop died. The
      // UI falls back to each proxy's last health check rather than being
      // handed a guess.
    );
    _publish(next);
  }

  void _onNativeStats(Map<dynamic, dynamic> event) {
    if (_disposed) return;
    final next = ThroughputStats(
      bytesDown: (event['bytesDown'] as num?)?.toInt() ?? 0,
      bytesUp: (event['bytesUp'] as num?)?.toInt() ?? 0,
      downBytesPerSecond: (event['downBytesPerSecond'] as num?)?.toDouble() ?? 0,
      upBytesPerSecond: (event['upBytesPerSecond'] as num?)?.toDouble() ?? 0,
      sessionDuration:
          Duration(seconds: (event['sessionSeconds'] as num?)?.toInt() ?? 0),
      measuredLatencyMs: (event['latencyMs'] as num?)?.toInt(),
    );
    _stats = next;
    if (!_statsController.isClosed) _statsController.add(next);
  }

  void _publish(EngineStatus next) {
    _status = next;
    if (!_statusController.isClosed) _statusController.add(next);

    final settling = _settling;
    if (settling != null && !settling.isCompleted) {
      switch (next.state) {
        case EngineState.connected:
        case EngineState.disconnected:
          settling.complete();
        case EngineState.error:
          settling.completeError(
            EngineException(next.failure ?? EngineFailure.unknown, next.detail),
          );
        case EngineState.connecting:
        case EngineState.disconnecting:
          break;
      }
    }
  }

  static EngineState _stateFromWire(String value) => switch (value) {
        'connecting' => EngineState.connecting,
        'connected' => EngineState.connected,
        'disconnecting' => EngineState.disconnecting,
        'error' => EngineState.error,
        _ => EngineState.disconnected,
      };

  static EngineFailure _failureFromWire(String value) => switch (value) {
        'permissionDenied' => EngineFailure.permissionDenied,
        'hopUnreachable' => EngineFailure.hopUnreachable,
        'authenticationFailed' => EngineFailure.authenticationFailed,
        'entitlementRejected' => EngineFailure.entitlementRejected,
        'networkUnavailable' => EngineFailure.networkUnavailable,
        _ => EngineFailure.unknown,
      };

  @override
  Future<void> connect(ProxyChain chain, {ChainCredentials? credentials}) async {
    if (_status.isConnected && _status.chain == chain) return;
    if (_status.isBusy) return;

    // Build the config before asking for anything: a chain that cannot be
    // expressed should fail here, with a precise message, rather than after
    // the user has approved a VPN prompt.
    final TunnelPlan plan;
    try {
      plan = _configBuilder.build(chain, credentials: credentials);
    } on XrayConfigException catch (error) {
      _failNow(EngineFailure.hopUnreachable, error.message, chain: chain);
      throw EngineException(EngineFailure.hopUnreachable, error.message);
    }

    _chain = chain;
    _publish(
      EngineStatus(
        state: EngineState.connecting,
        chain: chain,
        progress: 0,
        detail: 'Requesting tunnel permission',
      ),
    );

    try {
      // VpnService.prepare() returns null when consent was already given.
      // The Intent it returns otherwise has to be launched from an
      // Activity, which is why this is two calls and not one.
      var granted = await _methods.invokeMethod<bool>('prepare') ?? false;
      if (!granted) {
        granted =
            await _methods.invokeMethod<bool>('requestPermission') ?? false;
      }
      if (!granted) {
        const detail =
            'Android needs your permission to route this device through the '
            'tunnel. Without it no traffic can be proxied.';
        _failNow(EngineFailure.permissionDenied, detail, chain: chain);
        throw const EngineException(EngineFailure.permissionDenied, detail);
      }
    } on PlatformException catch (error) {
      final detail = error.message ?? 'The VPN permission request failed.';
      _failNow(EngineFailure.permissionDenied, detail, chain: chain);
      throw EngineException(EngineFailure.permissionDenied, detail);
    }

    final settling = Completer<void>();
    _settling = settling;

    try {
      await _methods.invokeMethod<void>('start', plan.toMethodArguments());
    } on PlatformException catch (error) {
      _settling = null;
      final detail = error.message ?? 'The tunnel service refused to start.';
      _failNow(_failureFromWire(error.code), detail, chain: chain);
      throw EngineException(_failureFromWire(error.code), detail);
    }

    try {
      await settling.future.timeout(connectTimeout);
    } on TimeoutException {
      const detail =
          'The chain did not finish connecting in time. A hop is probably '
          'unreachable.';
      // Fail closed: ask the service to tear down rather than leaving a
      // half-built tunnel up with nobody watching it.
      unawaited(_invokeQuietly('stop'));
      _failNow(EngineFailure.hopUnreachable, detail, chain: chain);
      throw const EngineException(EngineFailure.hopUnreachable, detail);
    } finally {
      if (identical(_settling, settling)) _settling = null;
    }
  }

  void _failNow(
    EngineFailure failure,
    String detail, {
    required ProxyChain chain,
  }) {
    _publish(
      EngineStatus(
        state: EngineState.error,
        chain: chain,
        failure: failure,
        detail: detail,
      ),
    );
  }

  @override
  Future<void> disconnect() async {
    if (_status.state == EngineState.disconnected) return;

    final settling = Completer<void>();
    _settling = settling;
    try {
      await _methods.invokeMethod<void>('stop');
      await settling.future.timeout(disconnectTimeout);
    } on TimeoutException {
      // The service is gone or wedged. Report disconnected rather than
      // leaving the UI stuck on "Disconnecting" forever; the service's own
      // teardown is idempotent.
      _publish(const EngineStatus.idle());
    } on PlatformException {
      _publish(const EngineStatus.idle());
    } finally {
      if (identical(_settling, settling)) _settling = null;
      _chain = null;
    }
  }

  Future<void> _invokeQuietly(String method) async {
    try {
      await _methods.invokeMethod<void>(method);
    } on PlatformException {
      // Best effort.
    } on MissingPluginException {
      // Best effort.
    }
  }

  /// Debug-only: hands a generated config to libXray's `testXray`, which
  /// constructs an instance without starting it.
  ///
  /// This is how the config generator is checked against the real parser
  /// without a device ever establishing a TUN. Returns null when the engine
  /// accepted the config, or the engine's own error string when it did not.
  @visibleForTesting
  Future<String?> validateConfig(String configJson) async {
    try {
      return await _methods.invokeMethod<String>('validateConfig', <String, Object?>{
        'config': configJson,
      });
    } on PlatformException catch (error) {
      return error.message ?? error.code;
    }
  }

  @override
  Future<void> dispose() async {
    _disposed = true;
    await _statusSub?.cancel();
    await _statsSub?.cancel();
    _statusSub = null;
    _statsSub = null;
    await _statusController.close();
    await _statsController.close();
  }
}
