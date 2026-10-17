import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import '../../../data/models/chain_hop.dart';
import '../../../data/models/proxy_chain.dart';
import '../../../data/models/proxy_credentials.dart';
import '../../net/host_pin.dart';
import '../chain_preflight.dart';
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
    ChainPreflight Function()? preflightFactory,
    HostPin? hostPin,
    String? apiBaseUrl,
    HostResolver? hostResolver,
    this.resolveTimeout = defaultResolveTimeout,
    this.connectTimeout = defaultConnectTimeout,
    this.disconnectTimeout = defaultDisconnectTimeout,
    this.stopCallTimeout = defaultStopCallTimeout,
    this.validateTimeout = defaultValidateTimeout,
  })  : _methods = methodChannel ?? const MethodChannel(methodChannelName),
        _statusEvents = statusChannel ?? const EventChannel(statusChannelName),
        _statsEvents = statsChannel ?? const EventChannel(statsChannelName),
        _preflightFactory = preflightFactory ?? ChainPreflight.new,
        // An unshared, never-read pin when none was supplied: the pinning
        // path then runs identically and simply has no audience, which is
        // one branch fewer than making the field nullable.
        _hostPin = hostPin ?? HostPin(),
        _hostResolver = hostResolver ?? InternetAddress.lookup,
        // Null when the base URL is already an address — today's
        // `http://10.0.2.2:8000` — which is the no-op path: there is no name
        // to look up, so nothing is resolved and nothing is pinned.
        _apiHost = apiBaseUrl == null ? null : hostToPin(apiBaseUrl) {
    _attach();
  }

  static const String methodChannelName = 'com.nenobot.proxy_crack/vpn';
  static const String statusChannelName = 'com.nenobot.proxy_crack/vpn_status';
  static const String statsChannelName = 'com.nenobot.proxy_crack/vpn_stats';

  /// A three-hop chain dials three handshakes in series, each through the
  /// last. Generous, but not unbounded: a hung dial must not leave the UI
  /// spinning forever.
  static const Duration defaultConnectTimeout = Duration(seconds: 60);

  /// How long [disconnect] waits for the service to confirm the tunnel is
  /// down before reporting it down anyway.
  ///
  /// Short on purpose. The service closes its file descriptor on its own
  /// deadline whether or not the engine cooperates, so waiting longer here
  /// buys nothing and costs the user a spinner over a device they believe
  /// is still captured.
  static const Duration defaultDisconnectTimeout = Duration(seconds: 5);

  /// Ceiling on the `stop` platform call itself.
  ///
  /// Not the same thing as [defaultDisconnectTimeout]: a `MethodChannel`
  /// call whose platform side is wedged never returns, and awaiting it
  /// without a bound would make the off switch depend on a healthy platform
  /// thread.
  static const Duration defaultStopCallTimeout = Duration(seconds: 3);

  /// Ceiling on `testXray`, which parses a config and builds an instance.
  static const Duration defaultValidateTimeout = Duration(seconds: 10);

  /// Ceiling on the pre-resolution of the API host (P26).
  ///
  /// It runs on the ordinary network with no tunnel in the way, so a healthy
  /// device answers in milliseconds. The bound exists for the device that is
  /// not healthy — a captive portal, or a previous session's TUN still held
  /// — where the useful outcome is a quick refusal, not a long wait.
  static const Duration defaultResolveTimeout = Duration(seconds: 8);

  /// Injectable so a test can prove the *structure* — that every wait is
  /// bounded — in milliseconds instead of in wall-clock seconds.
  final Duration connectTimeout;
  final Duration disconnectTimeout;
  final Duration stopCallTimeout;
  final Duration validateTimeout;
  final Duration resolveTimeout;

  final XrayConfigBuilder _configBuilder;
  final ChainPreflight Function() _preflightFactory;

  /// Where the session's pinned API address is published for the transport
  /// to read.
  final HostPin _hostPin;

  /// The API hostname to pre-resolve, or null when the base URL is already
  /// an address and there is nothing to resolve.
  final String? _apiHost;

  /// Injectable resolver, so the P26 path can be tested without a
  /// nameserver.
  final HostResolver _hostResolver;

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

  /// Completed when the service reports a settled state for a connect.
  Completer<void>? _connectSettling;

  /// Kept apart from [_connectSettling] deliberately. They used to be one
  /// field, so a disconnect raised during a connect overwrote the connect's
  /// completer and left it to time out 60 seconds later — the off switch
  /// quietly stranding the thing it interrupted.
  Completer<void>? _disconnectSettling;

  /// Bumped by [disconnect]. A connect sequence that started before the
  /// bump abandons itself rather than carrying on to establish a tunnel the
  /// user has already asked to stop.
  int _connectGeneration = 0;

  ChainPreflight? _preflight;

  /// This app's own UID, as the decimal string the `process` routing rule
  /// matches. Resolved once; the UID cannot change while the process lives.
  String? _processName;
  bool _processNameResolved = false;

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

    // The pin lasts exactly as long as the session does, and `disconnected`
    // is the only state that ends one.
    //
    // Note what is *not* here: `error`. The service publishes an error while
    // still holding the interface closed when the engine dies under it, and
    // that is the state in which the app most needs its pinned address —
    // dropping the pin there would send the next API call's name lookup
    // into the chain that just died, which is the deadlock this whole
    // mechanism exists to remove.
    if (next.state == EngineState.disconnected) _hostPin.clear();

    final connecting = _connectSettling;
    if (connecting != null && !connecting.isCompleted) {
      switch (next.state) {
        case EngineState.connected:
        case EngineState.disconnected:
          connecting.complete();
        case EngineState.error:
          connecting.completeError(
            EngineException(next.failure ?? EngineFailure.unknown, next.detail),
          );
        case EngineState.connecting:
        case EngineState.disconnecting:
          break;
      }
    }

    // A disconnect settles only on `disconnected`. It deliberately does not
    // settle on `error`: the service publishes an error *while holding the
    // interface closed* when the engine dies under it (fail closed), and
    // treating that as "the tunnel is down" would report a device still
    // captured as free.
    final disconnecting = _disconnectSettling;
    if (disconnecting != null &&
        !disconnecting.isCompleted &&
        next.state == EngineState.disconnected) {
      disconnecting.complete();
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
        'providerBlocked' => EngineFailure.providerBlocked,
        'configRejected' => EngineFailure.configRejected,
        'authenticationFailed' => EngineFailure.authenticationFailed,
        'entitlementRejected' => EngineFailure.entitlementRejected,
        'networkUnavailable' => EngineFailure.networkUnavailable,
        _ => EngineFailure.unknown,
      };

  /// A connect is stale once [disconnect] has been called under it.
  bool _isStale(int generation) => _disposed || generation != _connectGeneration;

  /// Brings the tunnel up, in an order chosen so that nothing irreversible
  /// happens until the chain has shown it can carry a connection.
  ///
  /// 1. build the config,
  /// 2. hand it to the engine's own parser (`testXray`, no TUN),
  /// 3. resolve every name the session will need, and pin the API's,
  /// 4. walk the chain hop by hop over the physical network,
  /// 5. only then ask for VPN consent,
  /// 6. only then establish the TUN and move the default route.
  ///
  /// Steps 2 and 4 exist because of a real incident: the TUN was
  /// established and the default route moved *first*, and the engine then
  /// discovered it could not reach hop 2. The chain carried nothing, fail
  /// closed correctly held the interface up, and the device lost all
  /// connectivity with the app — the only thing that can turn the tunnel
  /// off — unable to reach its own backend.
  ///
  /// Step 3 closes the hole those two left (P26). The `process` rule keeps
  /// our *sockets* out of the chain but cannot keep our *name lookups* out
  /// of it, because `netd` resolves as root in another process and the rule
  /// matches our UID. Resolving before the TUN exists removes the
  /// dependency rather than trying to route around it. See
  /// `core/net/host_pin.dart` for why exempting netd's UID was rejected.
  ///
  /// Fail closed is unchanged **after** step 6. This moves *when* the
  /// tunnel is committed, not whether a live tunnel is allowed to leak.
  @override
  Future<void> connect(ProxyChain chain, {ChainCredentials? credentials}) async {
    if (_status.isConnected && _status.chain == chain) return;
    if (_status.isBusy) return;

    final generation = ++_connectGeneration;

    // The UID the `process` routing rule needs, so this app's own API calls
    // are not carried by the chain they are trying to build.
    final processName = await _appProcessName();
    if (_isStale(generation)) return;

    // Build the config before asking for anything: a chain that cannot be
    // expressed should fail here, with a precise message, rather than after
    // the user has approved a VPN prompt.
    final TunnelPlan plan;
    try {
      plan = _configBuilder.build(
        chain,
        credentials: credentials,
        exemptProcessName: processName,
      );
    } on XrayConfigException catch (error) {
      _failNow(EngineFailure.configRejected, error.message, chain: chain);
      throw EngineException(EngineFailure.configRejected, error.message);
    }

    _chain = chain;
    _publish(
      EngineStatus(
        state: EngineState.connecting,
        chain: chain,
        progress: 0.05,
        detail: 'Checking the route configuration',
      ),
    );

    // --- step 2: the engine's own parser, with no TUN ------------------
    final configError = await _validateConfig(plan.validationConfigJson);
    if (_isStale(generation)) return;
    if (configError != null) {
      final detail =
          'The tunnel engine rejected this route before it was started: '
          '$configError';
      _failNow(EngineFailure.configRejected, detail, chain: chain);
      throw EngineException(EngineFailure.configRejected, detail);
    }

    // --- step 3: resolve names while ordinary DNS still works -----------
    _publish(
      EngineStatus(
        state: EngineState.connecting,
        chain: chain,
        progress: 0.15,
        detail: 'Resolving addresses before the tunnel starts',
      ),
    );

    final InternetAddress? firstHopAddress;
    try {
      firstHopAddress = await _preResolve(chain);
    } on HostResolutionException catch (error) {
      // Refuse, rather than establish a tunnel the app may not be able to
      // reach its own backend through. A connect that does not happen is
      // recoverable; a device that cannot ask for the tunnel to stop is
      // not.
      _failNow(EngineFailure.networkUnavailable, error.message, chain: chain);
      throw EngineException(EngineFailure.networkUnavailable, error.message);
    }
    if (_isStale(generation)) return;

    // --- step 4: walk the chain on the physical network ----------------
    _publish(
      EngineStatus(
        state: EngineState.connecting,
        chain: chain,
        progress: 0.25,
        detail: chain.length > 1
            ? 'Checking hop 2 is reachable through hop 1'
            : 'Checking hop 1 answers',
      ),
    );

    final preflight = _preflightFactory();
    _preflight = preflight;
    final PreflightResult probe;
    try {
      probe = await preflight.run(
        chain,
        credentials: credentials,
        firstHopAddress: firstHopAddress,
      );
    } finally {
      if (identical(_preflight, preflight)) _preflight = null;
    }
    if (_isStale(generation)) return;
    if (!probe.isViable) {
      final failure = _failureFor(probe.outcome);
      _failNow(failure, probe.detail, chain: chain);
      throw EngineException(failure, probe.detail);
    }

    // --- step 5: consent ------------------------------------------------
    _publish(
      EngineStatus(
        state: EngineState.connecting,
        chain: chain,
        progress: 0.45,
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
      if (_isStale(generation)) return;
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

    // --- step 6: commit -------------------------------------------------
    final settling = Completer<void>();
    _connectSettling = settling;

    try {
      await _methods.invokeMethod<void>('start', plan.toMethodArguments());
    } on PlatformException catch (error) {
      _connectSettling = null;
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
      if (identical(_connectSettling, settling)) _connectSettling = null;
    }
  }

  /// P26: resolve, before the TUN exists, every name the session will need.
  ///
  /// Two names, treated differently on purpose.
  ///
  /// **The API host.** Pinned for the session, and a failure to resolve it
  /// refuses the connect. The app must be able to reach its own backend
  /// while the tunnel is up — to re-fetch credentials, to report usage, to
  /// sign out — and once the TUN is established there is no second chance
  /// to look the name up, because our own lookups are carried by the chain
  /// whatever the `process` rule says. If the base URL is already an
  /// address, `_apiHost` is null and this does nothing at all: no lookup,
  /// no pin, no behaviour change. That is today's dev case
  /// (`http://10.0.2.2:8000`).
  ///
  /// **Hop 1.** Resolved so the pre-flight walk can dial it without a
  /// lookup, but *best effort*: a failure here returns null and the walk
  /// dials by name exactly as it always did. A name that cannot be resolved
  /// is a hop that cannot be reached, and the walk already reports that
  /// precisely ("Hop 1 did not answer"). Turning it into a second, earlier
  /// refusal would only make the same failure arrive with a worse message.
  ///
  /// Hops 2..n are deliberately left as names: they are resolved by the
  /// previous hop, never by this device.
  Future<InternetAddress?> _preResolve(ProxyChain chain) async {
    final apiHost = _apiHost;
    if (apiHost != null) {
      final address = await resolveHostOnce(
        apiHost,
        resolver: _hostResolver,
        timeout: resolveTimeout,
      );
      _hostPin.pin(apiHost, address);
      if (kDebugMode) {
        // The address of our own backend is not a secret and not a user
        // identifier; knowing which one was pinned is the first thing
        // anyone debugging this will want.
        debugPrint('P26: pinned API host $apiHost for this session.');
      }
    }

    if (chain.isEmpty) return null;
    final hops = List<ChainHop>.of(chain.hops)
      ..sort((a, b) => a.position.compareTo(b.position));
    final host = hops.first.proxy.host;
    final literal = InternetAddress.tryParse(host);
    if (literal != null) return literal;
    try {
      return await resolveHostOnce(
        host,
        resolver: _hostResolver,
        timeout: resolveTimeout,
      );
    } on HostResolutionException {
      return null;
    }
  }

  static EngineFailure _failureFor(PreflightOutcome outcome) =>
      switch (outcome) {
        PreflightOutcome.providerBlocked => EngineFailure.providerBlocked,
        PreflightOutcome.authenticationFailed =>
          EngineFailure.authenticationFailed,
        PreflightOutcome.hopUnreachable => EngineFailure.hopUnreachable,
        // Never reached: both of these are viable outcomes.
        PreflightOutcome.reachable ||
        PreflightOutcome.inconclusive =>
          EngineFailure.unknown,
      };

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

  /// Tears the tunnel down. **Unconditional, local, and bounded.**
  ///
  /// Three properties, each of which was missing and each of which was a
  /// way to brick a device:
  ///
  /// * **Unconditional.** No early return on cached state. If this object
  ///   believes it is already disconnected while a service somewhere is
  ///   still holding a TUN, believing itself is precisely the wrong move —
  ///   the stop is cheap and idempotent, so it is always sent.
  /// * **Local.** Nothing here awaits an HTTP call, a credential fetch or
  ///   any value that only arrives over the network. The only thing it
  ///   touches is a platform channel into our own process.
  /// * **Bounded.** Both the platform call and the wait for confirmation
  ///   have their own ceilings, so a wedged engine or a wedged platform
  ///   thread delays the off switch by seconds rather than forever. The
  ///   service closes its file descriptor on its own deadline regardless of
  ///   what is observed here.
  @override
  Future<void> disconnect() async {
    // Abandon any connect sequence in flight first, so it cannot go on to
    // establish a tunnel after the user has asked for one to stop, and so a
    // pre-flight walk sitting on a socket gives it up immediately.
    _connectGeneration++;
    _preflight?.cancel();
    _preflight = null;

    _publish(
      EngineStatus(
        state: EngineState.disconnecting,
        chain: _chain,
        detail: 'Closing the tunnel',
      ),
    );

    final settling = Completer<void>();
    _disconnectSettling = settling;
    try {
      await _invokeQuietly('stop', timeout: stopCallTimeout);
      await settling.future.timeout(disconnectTimeout);
    } on TimeoutException {
      // The service never confirmed. It closes its descriptor on its own
      // deadline whatever happens here, and leaving the UI on
      // "Disconnecting" forever would hide the off switch behind a spinner.
    } finally {
      if (identical(_disconnectSettling, settling)) _disconnectSettling = null;
      _chain = null;
      _publish(const EngineStatus.idle());
    }
  }

  /// Best effort, and bounded: a platform call with a wedged other side
  /// never returns on its own.
  Future<void> _invokeQuietly(String method, {Duration? timeout}) async {
    try {
      final call = _methods.invokeMethod<void>(method);
      await (timeout == null ? call : call.timeout(timeout));
    } on Object {
      // Every failure mode here — no handler, a platform exception, a
      // timeout — means the same thing to the caller: we asked, and we are
      // not going to wait any longer.
    }
  }

  /// This app's own UID as the decimal string the `process` routing rule
  /// matches, or null when the platform cannot say.
  ///
  /// A null answer does not stop a connect. The exemption is one of three
  /// independent defences against the deadlock — the other two being the
  /// pre-flight walk, which keeps a broken chain from ever taking the
  /// route, and the unconditional local teardown — so losing it degrades
  /// the app rather than breaking it.
  Future<String?> _appProcessName() async {
    if (_processNameResolved) return _processName;
    _processNameResolved = true;
    try {
      final uid = await _methods
          .invokeMethod<int>('appUid')
          .timeout(const Duration(seconds: 3));
      if (uid != null && uid >= 0) _processName = '$uid';
    } on Object {
      // Desktop hosts and tests have no such method. No rule is emitted,
      // which is better than one that cannot match.
    }
    if (_processName == null) {
      debugPrint(
        'AndroidVpnEngine: no UID available; this app\'s own traffic will '
        'be carried by the chain.',
      );
    }
    return _processName;
  }

  /// Hands a config to libXray's `testXray`, which builds an instance and
  /// closes it without starting one.
  ///
  /// Returns null when the engine accepted it, or the engine's own error
  /// string when it did not. A failure to *ask* (no platform, a timeout) is
  /// reported as acceptance: a validation step that cannot run must not
  /// become a second way to refuse a working chain.
  Future<String?> _validateConfig(String configJson) async {
    try {
      return await _methods.invokeMethod<String>(
        'validateConfig',
        <String, Object?>{'config': configJson},
      ).timeout(validateTimeout);
    } on PlatformException catch (error) {
      return error.message ?? error.code;
    } on Object {
      return null;
    }
  }

  /// Exposed so an on-device check can put a config through the real parser
  /// without a connect. The connect path uses [_validateConfig].
  @visibleForTesting
  Future<String?> validateConfig(String configJson) =>
      _validateConfig(configJson);

  @override
  Future<void> dispose() async {
    _disposed = true;
    _preflight?.cancel();
    _preflight = null;
    _hostPin.clear();
    await _statusSub?.cancel();
    await _statsSub?.cancel();
    _statusSub = null;
    _statsSub = null;
    await _statusController.close();
    await _statsController.close();
  }
}
