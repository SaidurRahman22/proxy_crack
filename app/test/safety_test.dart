import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proxy_crack/app/app_controller.dart';
import 'package:proxy_crack/core/net/host_pin.dart';
import 'package:proxy_crack/core/theme/app_theme.dart';
import 'package:proxy_crack/features/connection/widgets/connect_button.dart';
import 'package:proxy_crack/core/routing/chain_preflight.dart';
import 'package:proxy_crack/core/routing/mock_proxy_engine.dart';
import 'package:proxy_crack/core/routing/platform/android_vpn_engine.dart';
import 'package:proxy_crack/core/routing/proxy_engine.dart';
import 'package:proxy_crack/core/routing/xray_config_builder.dart';
import 'package:proxy_crack/data/mock/mock_data.dart';
import 'package:proxy_crack/data/models/account_snapshot.dart';
import 'package:proxy_crack/data/models/announcement.dart';
import 'package:proxy_crack/data/models/chain_diversity.dart';
import 'package:proxy_crack/data/models/chain_hop.dart';
import 'package:proxy_crack/data/models/country_code.dart';
import 'package:proxy_crack/data/models/entitlements.dart';
import 'package:proxy_crack/data/models/proxy.dart';
import 'package:proxy_crack/data/models/proxy_chain.dart';
import 'package:proxy_crack/data/models/proxy_credentials.dart';
import 'package:proxy_crack/data/models/resolved_chain.dart';
import 'package:proxy_crack/data/models/saved_chain.dart';
import 'package:proxy_crack/data/repositories/catalog_repository.dart';

/// Regression tests for the three defects that followed one incident.
///
/// The operator connected a multi-hop chain on an inventory where hop 1
/// cannot reach hop 2. The chain established, carried nothing, fail closed
/// correctly held the interface up — and **the device lost all connectivity
/// with no way out from inside the app**. It took `adb shell am force-stop`.
///
/// Three separate things had to be true for that to happen, and each one is
/// pinned here:
///
/// 1. the off switch could be blocked by the network it was switching off;
/// 2. the TUN was committed before anything checked the chain could carry
///    traffic;
/// 3. the app's own API traffic was routed through the chain it was
///    building, so a broken chain cut the app off from its own backend.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  // ---------------------------------------------------------------------
  // Fixtures
  // ---------------------------------------------------------------------

  Proxy proxy({
    required int id,
    required String host,
    required int port,
    ProxyProtocol protocol = ProxyProtocol.http,
  }) =>
      Proxy(
        id: id,
        protocol: protocol,
        host: host,
        port: port,
        country: 'Germany',
        countryCode: CountryCode.fromCountry('Germany'),
        city: 'Frankfurt',
        tier: ProxyTier.free,
        isAlive: true,
        latencyMs: 40,
        lastChecked: DateTime.fromMillisecondsSinceEpoch(0),
      );

  ProxyChain chainOf(List<Proxy> proxies) => ProxyChain(
        id: 1,
        userId: 1,
        name: 'Test chain',
        createdAt: DateTime.fromMillisecondsSinceEpoch(0),
        hops: <ChainHop>[
          for (var i = 0; i < proxies.length; i++)
            ChainHop(id: i + 1, chainId: 1, position: i, proxy: proxies[i]),
        ],
      );

  // ---------------------------------------------------------------------
  // 1. The off switch never depends on the network
  // ---------------------------------------------------------------------

  group('disconnect is unconditional and local', () {
    test('AppController.disconnect completes with the API unreachable',
        () async {
      // The backend is not merely slow — it never answers at all. This is
      // the state the device was actually in: the chain could not carry the
      // app's own API calls, so every request hung until its timeout.
      final repository = _HangingRepository();
      final controller = AppController(
        engine: MockProxyEngine(speedMultiplier: 1000),
        repository: repository,
      );
      addTearDown(controller.dispose);

      await controller.load();
      controller.addHop(repository.pool.firstWhere((p) => p.isAlive));

      // Start a connect and leave it parked on the unanswerable call.
      unawaited(controller.connect());
      await Future<void>.delayed(const Duration(milliseconds: 20));
      expect(
        controller.connectStage,
        ConnectStage.resolving,
        reason: 'the connect must be genuinely in flight for this to mean '
            'anything',
      );
      expect(repository.resolveCalls, 1);
      expect(repository.resolveIsPending, isTrue);

      // The whole point: this returns, and it returns promptly, while the
      // API is still unreachable.
      final stopwatch = Stopwatch()..start();
      await controller.disconnect().timeout(const Duration(seconds: 2));
      stopwatch.stop();

      expect(controller.connectStage, ConnectStage.idle);
      expect(controller.status.state, EngineState.disconnected);
      expect(
        stopwatch.elapsed,
        lessThan(const Duration(seconds: 1)),
        reason: 'disconnect waited on something it should not have',
      );

      // And the abandoned connect cannot come back to life. Letting the
      // API answer late must not start a tunnel the user cancelled.
      repository.answerResolve();
      await Future<void>.delayed(const Duration(milliseconds: 50));
      expect(
        repository.credentialCalls,
        0,
        reason: 'a cancelled connect went on to spend the credential budget',
      );
      expect(controller.status.state, EngineState.disconnected);
      expect(controller.connectStage, ConnectStage.idle);
    });

    test('the control is never refused while a connect is in flight',
        () async {
      // `toggleConnection` used to return early whenever the engine was
      // busy, so during a connect attempt — exactly when a half-built chain
      // can take the device's network away — tapping the one control did
      // nothing at all.
      final repository = _HangingRepository();
      final controller = AppController(
        engine: MockProxyEngine(speedMultiplier: 1000),
        repository: repository,
      );
      addTearDown(controller.dispose);

      await controller.load();
      controller.addHop(repository.pool.firstWhere((p) => p.isAlive));

      unawaited(controller.toggleConnection());
      await Future<void>.delayed(const Duration(milliseconds: 20));
      expect(controller.connectStage, isNot(ConnectStage.idle));

      await controller.toggleConnection().timeout(const Duration(seconds: 2));

      expect(controller.connectStage, ConnectStage.idle);
      expect(controller.status.state, EngineState.disconnected);
    });

    test('a cancel mid-handshake does not become a connection', () async {
      // The engine reports `connecting` for as long as the hops take to
      // dial. A disconnect inside that window must win.
      final repository = _HangingRepository(answerImmediately: true);
      final controller = AppController(
        engine: MockProxyEngine(speedMultiplier: 4),
        repository: repository,
      );
      addTearDown(controller.dispose);

      await controller.load();
      controller.addHop(repository.pool.firstWhere((p) => p.isAlive));

      unawaited(controller.connect());
      await Future<void>.delayed(const Duration(milliseconds: 60));
      expect(controller.status.state, EngineState.connecting);

      await controller.toggleConnection().timeout(const Duration(seconds: 2));
      await Future<void>.delayed(const Duration(milliseconds: 400));

      expect(
        controller.status.state,
        EngineState.disconnected,
        reason: 'the cancelled connect reported itself connected anyway',
      );
    });

    testWidgets('the control stays live and offers Cancel while a connect is '
        'in flight', (tester) async {
      // Two separate failures lived here. The control was *disabled* while
      // the engine was busy, and — because it rendered the engine's own
      // state — during a retry it still read "RETRY" while a connect was
      // already running. Both were found on a device, at the exact moment a
      // user would be reaching for the off switch.
      var taps = 0;
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.dark,
          home: Scaffold(
            body: ConnectButton(
              // The engine still reports the previous attempt's failure...
              status: const EngineStatus(
                state: EngineState.error,
                failure: EngineFailure.providerBlocked,
              ),
              // ...while the app is already working on the next one.
              isWorking: true,
              onPressed: () => taps++,
            ),
          ),
        ),
      );
      await tester.pump();

      expect(find.text('CANCEL'), findsOneWidget);
      expect(find.text('RETRY'), findsNothing);

      await tester.tap(find.byType(InkWell));
      await tester.pump();
      expect(taps, 1, reason: 'the off switch was not tappable');
    });

    test('AndroidVpnEngine.disconnect completes when the platform never '
        'answers', () async {
      // A wedged platform thread: `stop` is accepted and never returns, and
      // no status event ever arrives. Both waits must be bounded, or the
      // off switch is bounded only by the user's patience.
      final harness = _ChannelHarness()
        ..handler = (call) {
          if (call.method == 'stop') return Completer<Object?>().future;
          return null;
        };
      addTearDown(harness.dispose);

      final engine = harness.engine();
      addTearDown(engine.dispose);

      final stopwatch = Stopwatch()..start();
      await engine.disconnect().timeout(const Duration(seconds: 2));
      stopwatch.stop();

      expect(engine.status.state, EngineState.disconnected);
      expect(harness.calls, contains('stop'));
      expect(stopwatch.elapsed, lessThan(const Duration(seconds: 1)));
    });

    test('disconnect is sent even when the engine believes it is already '
        'disconnected', () async {
      // The old early return trusted this object's own idea of the state.
      // If a service is still holding a TUN while this believes otherwise,
      // believing itself is the one thing that must not happen.
      final harness = _ChannelHarness();
      addTearDown(harness.dispose);

      final engine = harness.engine();
      addTearDown(engine.dispose);

      expect(engine.status.state, EngineState.disconnected);
      await engine.disconnect().timeout(const Duration(seconds: 2));

      expect(harness.calls.where((c) => c == 'stop'), hasLength(1));
    });
  });

  // ---------------------------------------------------------------------
  // 2. Nothing is committed until the chain is viable
  // ---------------------------------------------------------------------

  group('pre-flight runs before the TUN is committed', () {
    test('a config the engine rejects fails before prepare or start',
        () async {
      final harness = _ChannelHarness()
        ..handler = (call) {
          if (call.method == 'appUid') return 10234;
          if (call.method == 'validateConfig') {
            return 'failed to parse config: unknown cipher';
          }
          return null;
        };
      addTearDown(harness.dispose);

      final engine = harness.engine(
        preflight: () => _FakePreflight(_ok),
      );
      addTearDown(engine.dispose);

      final chain = chainOf(<Proxy>[proxy(id: 1, host: '10.0.0.1', port: 8080)]);

      await expectLater(
        engine.connect(chain),
        throwsA(
          isA<EngineException>().having(
            (e) => e.failure,
            'failure',
            EngineFailure.configRejected,
          ),
        ),
      );

      expect(harness.calls, contains('validateConfig'));
      // The proof that no TUN was established: the two calls that could
      // have produced one were never made.
      expect(
        harness.calls,
        isNot(contains('prepare')),
        reason: 'VPN consent was requested for a config the engine refused',
      );
      expect(
        harness.calls,
        isNot(contains('start')),
        reason: 'the tunnel service was started with a rejected config',
      );
      expect(engine.status.state, EngineState.error);
    });

    test('a provider block at hop 2 stops the connect before start',
        () async {
      // The incident, reproduced: hop 1 answers 403 to a CONNECT aimed at
      // hop 2. Nothing may be routed, and the error must name the cause.
      final hop1 = await _fakeHttpProxy('HTTP/1.1 403 Forbidden\r\n\r\n');
      addTearDown(hop1.close);

      final harness = _ChannelHarness()
        ..handler = (call) {
          if (call.method == 'appUid') return 10234;
          if (call.method == 'validateConfig') return null;
          return null;
        };
      addTearDown(harness.dispose);

      final engine = harness.engine();
      addTearDown(engine.dispose);

      final chain = chainOf(<Proxy>[
        proxy(id: 1, host: '127.0.0.1', port: hop1.port),
        proxy(id: 2, host: '203.0.113.10', port: 8080),
      ]);

      await expectLater(
        engine.connect(chain),
        throwsA(
          isA<EngineException>().having(
            (e) => e.failure,
            'failure',
            EngineFailure.providerBlocked,
          ),
        ),
      );

      expect(harness.calls, isNot(contains('start')));
      expect(harness.calls, isNot(contains('prepare')));
      expect(engine.status.failure, EngineFailure.providerBlocked);
      expect(engine.status.detail, contains('provider'));
    });

    test('an unreachable second hop stops the connect before start',
        () async {
      final harness = _ChannelHarness()
        ..handler = (call) {
          if (call.method == 'appUid') return 10234;
          if (call.method == 'validateConfig') return null;
          return null;
        };
      addTearDown(harness.dispose);

      final engine = harness.engine(
        preflight: () => _FakePreflight(
          const PreflightResult(
            outcome: PreflightOutcome.hopUnreachable,
            detail: 'Hop 1 could not reach hop 2.',
            hopsProbed: 1,
            failingHopNumber: 2,
          ),
        ),
      );
      addTearDown(engine.dispose);

      final chain = chainOf(<Proxy>[
        proxy(id: 1, host: '10.0.0.1', port: 8080),
        proxy(id: 2, host: '10.0.0.2', port: 8080),
      ]);

      await expectLater(
        engine.connect(chain),
        throwsA(
          isA<EngineException>().having(
            (e) => e.failure,
            'failure',
            EngineFailure.hopUnreachable,
          ),
        ),
      );
      expect(harness.calls, isNot(contains('start')));
    });

    test('a viable chain does reach start', () async {
      // The counterweight: the gate must let a good chain through, or it is
      // just a different way to be broken.
      final harness = _ChannelHarness()
        ..handler = (call) {
          if (call.method == 'appUid') return 10234;
          if (call.method == 'validateConfig') return null;
          if (call.method == 'prepare') return true;
          if (call.method == 'start') return true;
          return null;
        };
      addTearDown(harness.dispose);

      final engine = harness.engine(preflight: () => _FakePreflight(_ok));
      addTearDown(engine.dispose);

      final chain = chainOf(<Proxy>[proxy(id: 1, host: '10.0.0.1', port: 8080)]);
      final connecting = engine.connect(chain);
      await Future<void>.delayed(const Duration(milliseconds: 40));

      expect(harness.calls, contains('start'));
      harness.emitStatus(<String, Object?>{'state': 'connected'});
      await connecting.timeout(const Duration(seconds: 2));
      expect(engine.status.state, EngineState.connected);
    });
  });

  // ---------------------------------------------------------------------
  // 2b. P26 — our own DNS is not carried by the chain either
  // ---------------------------------------------------------------------

  group('the API host is pinned before the TUN exists', () {
    // The `process` rule keeps our *sockets* off the chain by matching our
    // UID. It cannot keep our *name lookups* off it: on API 29+ an app's
    // DNS goes through `netd`, which runs as root (UID 0) in another
    // process, so those packets carry netd's UID and our rule can never
    // match them. Exempting netd would take every app's DNS off the tunnel
    // — a device-wide DNS leak — so the fix is to need no DNS at all while
    // the tunnel is up.

    Future<void> settle() =>
        Future<void>.delayed(const Duration(milliseconds: 60));

    _ChannelHarness goodHarness() => _ChannelHarness()
      ..handler = (call) {
        if (call.method == 'appUid') return 10234;
        if (call.method == 'validateConfig') return null;
        if (call.method == 'prepare') return true;
        if (call.method == 'start') return true;
        return null;
      };

    test('resolution happens before consent and before start', () async {
      final harness = goodHarness();
      addTearDown(harness.dispose);

      final pin = HostPin();
      final engine = harness.engine(
        preflight: () => _FakePreflight(_ok),
        hostPin: pin,
        apiBaseUrl: 'https://api.example.com',
        // Recorded into the same list as the platform calls, so the
        // ordering assertion below is about one timeline, not two.
        hostResolver: (host) async {
          harness.calls.add('resolve:$host');
          return <InternetAddress>[InternetAddress('203.0.113.7')];
        },
      );
      addTearDown(engine.dispose);

      final connecting = engine.connect(
        chainOf(<Proxy>[proxy(id: 1, host: '10.0.0.1', port: 8080)]),
      );
      await settle();
      harness.emitStatus(<String, Object?>{'state': 'connected'});
      await connecting.timeout(const Duration(seconds: 2));

      final resolved = harness.calls.indexOf('resolve:api.example.com');
      expect(resolved, isNonNegative, reason: 'the API host was never resolved');
      // `start` is what hands the config to the service that calls
      // `establish()`. Everything about this fix depends on the lookup
      // being finished by then.
      expect(resolved, lessThan(harness.calls.indexOf('start')));
      expect(resolved, lessThan(harness.calls.indexOf('prepare')));

      expect(pin.addressFor('api.example.com')!.address, '203.0.113.7');
    });

    test('an IP base URL is a no-op: nothing is looked up, nothing is pinned',
        () async {
      // Today's dev case. A lookup here would be a pointless round trip
      // that can only fail.
      final harness = goodHarness();
      addTearDown(harness.dispose);

      final pin = HostPin();
      final engine = harness.engine(
        preflight: () => _FakePreflight(_ok),
        hostPin: pin,
        apiBaseUrl: 'http://10.0.2.2:8000',
        hostResolver: (host) async {
          harness.calls.add('resolve:$host');
          return <InternetAddress>[InternetAddress('203.0.113.7')];
        },
      );
      addTearDown(engine.dispose);

      final connecting = engine.connect(
        chainOf(<Proxy>[proxy(id: 1, host: '10.0.0.1', port: 8080)]),
      );
      await settle();
      harness.emitStatus(<String, Object?>{'state': 'connected'});
      await connecting.timeout(const Duration(seconds: 2));

      expect(harness.calls.any((c) => c.startsWith('resolve:')), isFalse);
      expect(pin.isPinned, isFalse);
      // And the connect is otherwise completely unaffected.
      expect(harness.calls, contains('start'));
      expect(engine.status.state, EngineState.connected);
    });

    test('a resolution failure refuses to connect instead of establishing',
        () async {
      // The whole point of doing this before the TUN. A connect that does
      // not happen is recoverable; a device that cannot reach the backend
      // it needs in order to ask for the tunnel to stop is not.
      final harness = goodHarness();
      addTearDown(harness.dispose);

      final pin = HostPin();
      final engine = harness.engine(
        preflight: () => _FakePreflight(_ok),
        hostPin: pin,
        apiBaseUrl: 'https://api.example.com',
        hostResolver: (host) =>
            Future<List<InternetAddress>>.error(const SocketException('no dns')),
      );
      addTearDown(engine.dispose);

      await expectLater(
        engine.connect(chainOf(<Proxy>[proxy(id: 1, host: '10.0.0.1', port: 8080)])),
        throwsA(
          isA<EngineException>().having(
            (e) => e.failure,
            'failure',
            EngineFailure.networkUnavailable,
          ),
        ),
      );

      expect(harness.calls, isNot(contains('prepare')));
      expect(
        harness.calls,
        isNot(contains('start')),
        reason: 'a TUN was established for a session whose API host could '
            'not be resolved',
      );
      expect(pin.isPinned, isFalse);
      expect(engine.status.state, EngineState.error);
      expect(engine.status.detail, contains('api.example.com'));
    });

    test('hop 1 is handed to the walk as an address, so it needs no lookup',
        () async {
      // Pre-flight runs before the TUN in the ordinary case, but not on a
      // *later* attempt: the service publishes an error while still holding
      // the interface closed, and in that state a hostname hop would be
      // resolved by netd into a chain that carries nothing.
      final harness = goodHarness();
      addTearDown(harness.dispose);

      final fake = _FakePreflight(_ok);
      final engine = harness.engine(
        preflight: () => fake,
        hostPin: HostPin(),
        apiBaseUrl: 'http://10.0.2.2:8000',
        hostResolver: (host) async {
          harness.calls.add('resolve:$host');
          return <InternetAddress>[InternetAddress('198.51.100.4')];
        },
      );
      addTearDown(engine.dispose);

      final connecting = engine.connect(
        chainOf(<Proxy>[proxy(id: 1, host: 'hop1.example.net', port: 8080)]),
      );
      await settle();
      harness.emitStatus(<String, Object?>{'state': 'connected'});
      await connecting.timeout(const Duration(seconds: 2));

      expect(harness.calls, contains('resolve:hop1.example.net'));
      expect(fake.sawFirstHopAddress?.address, '198.51.100.4');
    });

    test('a hop that is already an address is not looked up', () async {
      final harness = goodHarness();
      addTearDown(harness.dispose);

      final fake = _FakePreflight(_ok);
      final engine = harness.engine(
        preflight: () => fake,
        hostPin: HostPin(),
        apiBaseUrl: 'http://10.0.2.2:8000',
        hostResolver: (host) async {
          harness.calls.add('resolve:$host');
          return <InternetAddress>[InternetAddress('198.51.100.4')];
        },
      );
      addTearDown(engine.dispose);

      final connecting = engine.connect(
        chainOf(<Proxy>[proxy(id: 1, host: '10.0.0.1', port: 8080)]),
      );
      await settle();
      harness.emitStatus(<String, Object?>{'state': 'connected'});
      await connecting.timeout(const Duration(seconds: 2));

      expect(harness.calls.any((c) => c.startsWith('resolve:')), isFalse);
      expect(fake.sawFirstHopAddress?.address, '10.0.0.1');
    });

    test('a hop that will not resolve does not become a second refusal',
        () async {
      // The walk already reports an unreachable hop 1 precisely. Refusing
      // earlier would only deliver the same failure with a worse message.
      final harness = goodHarness();
      addTearDown(harness.dispose);

      final fake = _FakePreflight(_ok);
      final engine = harness.engine(
        preflight: () => fake,
        hostPin: HostPin(),
        apiBaseUrl: 'http://10.0.2.2:8000',
        hostResolver: (host) =>
            Future<List<InternetAddress>>.error(const SocketException('no dns')),
      );
      addTearDown(engine.dispose);

      final connecting = engine.connect(
        chainOf(<Proxy>[proxy(id: 1, host: 'hop1.example.net', port: 8080)]),
      );
      await settle();
      harness.emitStatus(<String, Object?>{'state': 'connected'});
      await connecting.timeout(const Duration(seconds: 2));

      expect(fake.ran, isTrue);
      expect(
        fake.sawFirstHopAddress,
        isNull,
        reason: 'the walk should fall back to dialling hop 1 by name',
      );
    });

    test('the pin survives an error that holds the interface closed, and is '
        'dropped only when the tunnel is really down', () async {
      // The service publishes an error *while still holding the TUN* when
      // the engine dies under it. That is the exact state in which the app
      // needs its pinned address — dropping it there would send the next
      // API lookup into the chain that just died.
      final harness = goodHarness();
      addTearDown(harness.dispose);

      final pin = HostPin();
      final engine = harness.engine(
        preflight: () => _FakePreflight(_ok),
        hostPin: pin,
        apiBaseUrl: 'https://api.example.com',
        hostResolver: (host) async =>
            <InternetAddress>[InternetAddress('203.0.113.7')],
      );
      addTearDown(engine.dispose);

      final connecting = engine.connect(
        chainOf(<Proxy>[proxy(id: 1, host: '10.0.0.1', port: 8080)]),
      );
      await settle();
      harness.emitStatus(<String, Object?>{'state': 'connected'});
      await connecting.timeout(const Duration(seconds: 2));
      expect(pin.isPinned, isTrue);

      harness.emitStatus(<String, Object?>{
        'state': 'error',
        'failure': 'hopUnreachable',
        'detail': 'The chain stopped carrying traffic.',
      });
      await settle();
      expect(
        pin.addressFor('api.example.com')!.address,
        '203.0.113.7',
        reason: 'the pin was dropped while the interface was still held',
      );

      await engine.disconnect().timeout(const Duration(seconds: 2));
      expect(pin.isPinned, isFalse);
    });
  });

  // ---------------------------------------------------------------------
  // The prober itself, against real sockets
  // ---------------------------------------------------------------------

  group('ChainPreflight', () {
    ProxyChain twoHops(int hop1Port, {ProxyProtocol protocol = ProxyProtocol.http}) =>
        chainOf(<Proxy>[
          proxy(id: 1, host: '127.0.0.1', port: hop1Port, protocol: protocol),
          proxy(id: 2, host: '203.0.113.10', port: 8080),
        ]);

    test('403 from hop 1 is a provider block, named as one', () async {
      final server = await _fakeHttpProxy('HTTP/1.1 403 Forbidden\r\n\r\n');
      addTearDown(server.close);

      final result = await ChainPreflight().run(twoHops(server.port));

      expect(result.outcome, PreflightOutcome.providerBlocked);
      expect(result.isViable, isFalse);
      expect(result.failingHopNumber, 2);
      // The sentence has to carry the cause, not just a code. This is the
      // thing the operator had to work out by hand.
      expect(result.detail, contains('403'));
      expect(result.detail, contains('provider'));
      expect(result.detail.toLowerCase(), contains('own ip ranges'));
    });

    test('SOCKS5 "not allowed by ruleset" is the same block', () async {
      final server = await _fakeSocksProxy(replyCode: 0x02);
      addTearDown(server.close);

      final result = await ChainPreflight()
          .run(twoHops(server.port, protocol: ProxyProtocol.socks5));

      expect(result.outcome, PreflightOutcome.providerBlocked);
      expect(result.detail, contains('provider'));
    });

    test('a 502 from hop 1 is an unreachable hop, not a provider block',
        () async {
      // These two must not be collapsed: one means "replace the proxy", the
      // other means "change provider".
      final server = await _fakeHttpProxy('HTTP/1.1 502 Bad Gateway\r\n\r\n');
      addTearDown(server.close);

      final result = await ChainPreflight().run(twoHops(server.port));

      expect(result.outcome, PreflightOutcome.hopUnreachable);
      expect(result.failingHopNumber, 2);
    });

    test('a hop 1 that does not answer at all fails at hop 1', () async {
      final port = await _closedPort();
      final result = await ChainPreflight(
        connectTimeout: const Duration(seconds: 2),
      ).run(twoHops(port));

      expect(result.outcome, PreflightOutcome.hopUnreachable);
      expect(result.failingHopNumber, 1);
    });

    test('200 Connection Established is viable', () async {
      final server =
          await _fakeHttpProxy('HTTP/1.1 200 Connection Established\r\n\r\n');
      addTearDown(server.close);

      final result = await ChainPreflight().run(twoHops(server.port));

      expect(result.outcome, PreflightOutcome.reachable);
      expect(result.isViable, isTrue);
      expect(result.hopsProbed, 2);
    });

    test('407 is reported as the credential failure it is', () async {
      final server = await _fakeHttpProxy(
        'HTTP/1.1 407 Proxy Authentication Required\r\n\r\n',
      );
      addTearDown(server.close);

      final result = await ChainPreflight().run(twoHops(server.port));

      expect(result.outcome, PreflightOutcome.authenticationFailed);
      expect(result.failingHopNumber, 1);
    });

    test('a Shadowsocks first hop is inconclusive, and inconclusive passes',
        () async {
      // P14: a Shadowsocks server answers a wrong key and a right key with
      // identical silence. "We could not check" must not become "refused".
      final server =
          await _fakeHttpProxy('HTTP/1.1 200 Connection Established\r\n\r\n');
      addTearDown(server.close);

      final result = await ChainPreflight().run(
        twoHops(server.port, protocol: ProxyProtocol.shadowsocks),
      );

      expect(result.outcome, PreflightOutcome.inconclusive);
      expect(result.isViable, isTrue);
      expect(result.detail.toLowerCase(), contains('shadowsocks'));
    });

    test('cancel releases a walk that is waiting on a silent hop', () async {
      // A hop that accepts the connection and then says nothing at all. The
      // walk would otherwise sit out its own timeout, and a disconnect
      // pressed during pre-flight would wait with it.
      final server = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
      final sockets = <Socket>[];
      server.listen(sockets.add);
      addTearDown(() async {
        for (final socket in sockets) {
          socket.destroy();
        }
        await server.close();
      });

      final preflight = ChainPreflight(
        totalTimeout: const Duration(seconds: 30),
        handshakeTimeout: const Duration(seconds: 30),
      );
      final walking = preflight.run(twoHops(server.port));
      await Future<void>.delayed(const Duration(milliseconds: 100));

      final stopwatch = Stopwatch()..start();
      preflight.cancel();
      await walking.timeout(const Duration(seconds: 3));
      stopwatch.stop();

      expect(
        stopwatch.elapsed,
        lessThan(const Duration(seconds: 2)),
        reason: 'cancel did not release the socket',
      );
    });
  });

  // ---------------------------------------------------------------------
  // 3. Our own API traffic is not carried by the chain
  // ---------------------------------------------------------------------

  group('process exemption', () {
    const builder = XrayConfigBuilder();

    List<Map<String, Object?>> rulesOf(Map<String, Object?> config) =>
        ((config['routing']! as Map<String, Object?>)['rules']! as List<Object?>)
            .cast<Map<String, Object?>>();

    test('the exemption is the first rule, scoped to one UID and direct',
        () async {
      final config = builder.buildConfigMap(
        chainOf(<Proxy>[proxy(id: 1, host: '10.0.0.1', port: 8080)]),
        exemptProcessName: '10234',
      );
      final rules = rulesOf(config);

      expect(rules.first['process'], <String>['10234']);
      expect(rules.first['outboundTag'], XrayConfigBuilder.directOutboundTag);
      expect(rules.first['inboundTag'], <String>[XrayConfigBuilder.tunInboundTag]);

      // Ahead of the DNS hijack on purpose: exempting the API connection
      // but still sending our own name lookups into a dead chain leaves the
      // same deadlock for any hostname-based API URL.
      final dnsRuleIndex = rules.indexWhere((r) => r['port'] == 53);
      expect(dnsRuleIndex, greaterThan(0));
    });

    test('it is the only direct rule, and matches nothing else', () async {
      final config = builder.buildConfigMap(
        chainOf(<Proxy>[
          proxy(id: 1, host: '10.0.0.1', port: 8080),
          proxy(id: 2, host: '10.0.0.2', port: 1080,
              protocol: ProxyProtocol.socks5),
        ]),
        exemptProcessName: '10234',
      );
      final direct = rulesOf(config)
          .where((r) => r['outboundTag'] == XrayConfigBuilder.directOutboundTag)
          .toList();

      // This must never become a general direct fallback: one rule, keyed
      // on one process.
      expect(direct, hasLength(1));
      expect(direct.single['process'], <String>['10234']);
      expect(direct.single.containsKey('domain'), isFalse);
      expect(direct.single.containsKey('ip'), isFalse);
    });

    test('no UID means no rule, not a rule that matches nothing', () async {
      // Desktop (D8) has no `registerProcessFinder` — it is compiled under
      // `//go:build android` — so every other platform passes null and must
      // get a config with no process rule in it at all.
      final config = builder.buildConfigMap(
        chainOf(<Proxy>[proxy(id: 1, host: '10.0.0.1', port: 8080)]),
      );
      expect(
        rulesOf(config).any((r) => r.containsKey('process')),
        isFalse,
      );
      expect(
        rulesOf(config)
            .any((r) => r['outboundTag'] == XrayConfigBuilder.directOutboundTag),
        isFalse,
      );
    });

    test('the validation config drops the TUN inbound and the runtime fd',
        () {
      // `testXray` builds a real instance. The TUN inbound would adopt a
      // file descriptor that `establish()` has not produced yet — and
      // running before `establish()` is the entire point.
      final plan = builder.build(
        chainOf(<Proxy>[proxy(id: 1, host: '10.0.0.1', port: 8080)]),
        exemptProcessName: '10234',
      );
      final validation =
          jsonDecode(plan.validationConfigJson) as Map<String, dynamic>;

      expect(validation['inbounds'], isEmpty);
      expect(validation.containsKey('env'), isFalse);
      // Everything a chain can get wrong is still there to be checked.
      expect(validation['outbounds'], isNotEmpty);
      expect(validation['routing'], isNotNull);
      expect(validation['dns'], isNotNull);

      // And the config that is actually run is untouched.
      final real = jsonDecode(plan.configJson) as Map<String, dynamic>;
      expect((real['inbounds']! as List<Object?>), hasLength(1));
      expect(
        (real['env']! as Map<String, dynamic>)[XrayConfigBuilder.tunFdEnvKey],
        XrayConfigBuilder.tunFdPlaceholder,
      );
    });
  });
}

const PreflightResult _ok = PreflightResult(
  outcome: PreflightOutcome.reachable,
  detail: 'ok',
  hopsProbed: 1,
);

class _FakePreflight extends ChainPreflight {
  _FakePreflight(this.result);

  final PreflightResult result;

  /// What the engine handed the walk for hop 1. Null means "dial by name".
  InternetAddress? sawFirstHopAddress;
  bool ran = false;

  @override
  Future<PreflightResult> run(
    ProxyChain chain, {
    ChainCredentials? credentials,
    InternetAddress? firstHopAddress,
  }) async {
    ran = true;
    sawFirstHopAddress = firstHopAddress;
    return result;
  }
}

// -----------------------------------------------------------------------
// Platform-channel harness
// -----------------------------------------------------------------------

/// Stands in for the Kotlin side so the engine's ordering can be asserted.
///
/// The event channels are mocked too: without a handler, subscribing throws
/// `MissingPluginException`, which the engine (correctly) turns into an
/// error state — and every test would then be measuring that instead.
class _ChannelHarness {
  _ChannelHarness() {
    _mock(_methodName, (call) async {
      calls.add(call.method);
      return handler?.call(call);
    });
    _mock(_statusName, (_) async => null);
    _mock(_statsName, (_) async => null);
  }

  static const String _methodName = 'test.proxy_crack/vpn';
  static const String _statusName = 'test.proxy_crack/vpn_status';
  static const String _statsName = 'test.proxy_crack/vpn_stats';

  final List<String> calls = <String>[];
  FutureOr<Object?> Function(MethodCall call)? handler;

  AndroidVpnEngine engine({
    ChainPreflight Function()? preflight,
    HostPin? hostPin,
    String? apiBaseUrl,
    HostResolver? hostResolver,
  }) =>
      AndroidVpnEngine(
        methodChannel: const MethodChannel(_methodName),
        statusChannel: const EventChannel(_statusName),
        statsChannel: const EventChannel(_statsName),
        preflightFactory: preflight,
        hostPin: hostPin,
        apiBaseUrl: apiBaseUrl,
        hostResolver: hostResolver,
        resolveTimeout: const Duration(milliseconds: 400),
        // Milliseconds rather than seconds: the property under test is
        // that every wait is bounded, not how long the bound is.
        connectTimeout: const Duration(milliseconds: 400),
        disconnectTimeout: const Duration(milliseconds: 60),
        stopCallTimeout: const Duration(milliseconds: 60),
        validateTimeout: const Duration(milliseconds: 400),
      );

  void emitStatus(Map<String, Object?> event) {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .handlePlatformMessage(
      _statusName,
      const StandardMethodCodec().encodeSuccessEnvelope(event),
      (_) {},
    );
  }

  void _mock(String name, Future<Object?> Function(MethodCall) h) {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(MethodChannel(name), h);
  }

  void dispose() {
    for (final name in <String>[_methodName, _statusName, _statsName]) {
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(MethodChannel(name), null);
    }
  }
}

// -----------------------------------------------------------------------
// Fake proxies, on real sockets
// -----------------------------------------------------------------------

Future<ServerSocket> _fakeHttpProxy(String response) async {
  final server = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
  server.listen((socket) {
    socket.listen(
      (_) {
        socket.add(ascii.encode(response));
        socket.flush();
      },
      onError: (Object _) {},
      cancelOnError: true,
    );
  });
  return server;
}

/// Answers the SOCKS5 greeting, then the CONNECT with [replyCode].
Future<ServerSocket> _fakeSocksProxy({required int replyCode}) async {
  final server = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
  server.listen((socket) {
    var greeted = false;
    socket.listen(
      (_) {
        if (!greeted) {
          greeted = true;
          socket.add(<int>[0x05, 0x00]);
        } else {
          socket.add(<int>[
            0x05, replyCode, 0x00, 0x01,
            0, 0, 0, 0,
            0, 0,
          ]);
        }
        socket.flush();
      },
      onError: (Object _) {},
      cancelOnError: true,
    );
  });
  return server;
}

/// A port nothing is listening on — bound and released, so it is real and
/// almost certainly still free.
Future<int> _closedPort() async {
  final server = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
  final port = server.port;
  await server.close();
  return port;
}

// -----------------------------------------------------------------------
// A repository that does not answer
// -----------------------------------------------------------------------

/// Models an unreachable backend precisely: calls are accepted and never
/// answered, which is what a request carried by a chain that drops packets
/// actually looks like from the client.
class _HangingRepository implements CatalogRepository {
  _HangingRepository({this.answerImmediately = false});

  /// When true, `resolveProxies` answers at once so a test can get past it
  /// and exercise a later stage.
  final bool answerImmediately;

  final List<Proxy> pool = MockData.proxies(count: 10);

  int resolveCalls = 0;
  int credentialCalls = 0;
  Completer<ResolvedChain>? _pendingResolve;

  bool get resolveIsPending {
    final pending = _pendingResolve;
    return pending != null && !pending.isCompleted;
  }

  void answerResolve() {
    final pending = _pendingResolve;
    if (pending != null && !pending.isCompleted) {
      pending.complete(
        MockData.resolve(
          MockData.savedChain(
            id: 1,
            name: 'x',
            proxyIds: <int>[pool.first.id],
            pool: pool,
          ),
        ),
      );
    }
  }

  @override
  Future<ResolvedChain> resolveProxies(List<int> proxyIds) {
    resolveCalls++;
    if (answerImmediately) {
      return Future<ResolvedChain>.value(
        MockData.resolve(
          MockData.savedChain(
            id: 1,
            name: 'x',
            proxyIds: proxyIds,
            pool: pool,
          ),
        ),
      );
    }
    final completer = Completer<ResolvedChain>();
    _pendingResolve = completer;
    return completer.future;
  }

  @override
  Future<ChainCredentials> fetchChainCredentials(ResolvedChain resolved) async {
    credentialCalls++;
    return ChainCredentials(<int, ProxyCredentials>{
      for (final hop in resolved.hops)
        hop.proxy.id: ProxyCredentials(
          proxyId: hop.proxy.id,
          username: 'u',
          password: 'p',
        ),
    });
  }

  @override
  Future<List<Proxy>> fetchProxies() async => pool;

  @override
  Future<AccountSnapshot> fetchAccount() async {
    final base = MockData.account;
    return AccountSnapshot(
      plan: base.plan,
      subscription: base.subscription,
      entitlements: const Entitlements(
        maxChainLength: 3,
        maxDevices: 3,
        allowedProxyTiers: <String>['free', 'premium'],
        maxProxyTier: 'premium',
        bandwidthLimitBytes: 0,
        bandwidthUsedBytes: 0,
        bandwidthRemainingBytes: null,
        isFreeTier: false,
        source: 'subscription',
      ),
      availablePlans: base.availablePlans,
      hasSubscriptionRow: true,
    );
  }

  @override
  Future<List<Announcement>> fetchAnnouncements() async =>
      const <Announcement>[];

  @override
  Future<List<SavedChain>> fetchChains() async => const <SavedChain>[];

  @override
  Future<SavedChain> saveChain({
    int? id,
    required String name,
    required List<int> proxyIds,
  }) async =>
      MockData.savedChain(
        id: id ?? 1,
        name: name,
        proxyIds: proxyIds,
        pool: pool,
      );

  @override
  Future<void> deleteChain(int id) async {}

  @override
  Future<ResolvedChain> resolveChain(int chainId) =>
      resolveProxies(<int>[pool.first.id]);

  @override
  Future<ChainDiversityReport> previewDiversity(List<int> proxyIds) async =>
      MockData.diversity(proxyIds, pool);

  @override
  Future<ChainDiversityReport> fetchChainDiversity(int chainId) async =>
      MockData.diversity(const <int>[], pool);

  @override
  Future<void> reportUsage({
    required int bytesUp,
    required int bytesDown,
    required DateTime sessionStart,
    required DateTime sessionEnd,
  }) async {}
}
