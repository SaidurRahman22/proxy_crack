import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:proxy_crack/data/api/api_failure.dart';
import 'package:proxy_crack/data/api/token_store.dart';
import 'package:proxy_crack/data/api/transport.dart';

import 'fake_transport.dart';

const String refreshPath = '/api/auth/refresh/';

void main() {
  group('AuthRefreshTransport', () {
    late InMemoryTokenStore store;
    late int sessionLostCount;

    AuthRefreshHooks hooksFor(InMemoryTokenStore store) => AuthRefreshHooks(
      readRefreshToken: store.readRefreshToken,
      storeTokens: store.write,
      onSessionLost: () async => sessionLostCount++,
    );

    setUp(() async {
      store = InMemoryTokenStore();
      sessionLostCount = 0;
      await store.write(
        const TokenPair(access: 'access-0', refresh: 'refresh-0'),
      );
    });

    test('refreshes once on 401 and retries the request exactly once', () async {
      var protectedCalls = 0;
      final base = FakeTransport((spec, index) async {
        if (spec.path == refreshPath) {
          return <String, dynamic>{
            'access': 'access-1',
            'refresh': 'refresh-1',
          };
        }
        protectedCalls++;
        if (protectedCalls == 1) {
          throwApi(
            401,
            errorEnvelope(
              code: 'not_authenticated',
              status: 401,
              detail: 'Given token not valid for any token type',
            ),
          );
        }
        return <String, dynamic>{'ok': true};
      });

      final transport = AuthRefreshTransport(base, hooksFor(store));
      final result = await transport.send(
        const RequestSpec(method: HttpMethod.get, path: '/api/proxies/'),
      );

      expect(result, <String, dynamic>{'ok': true});
      expect(protectedCalls, 2, reason: 'exactly one retry');
      expect(base.countFor(refreshPath), 1);
      expect(sessionLostCount, 0);
    });

    test(
      'stores the ROTATED refresh token, not just the access token',
      () async {
        // SIMPLE_JWT has ROTATE_REFRESH_TOKENS and BLACKLIST_AFTER_ROTATION
        // both on, so `refresh-0` is dead the instant the server answers. A
        // client that kept it is signed out at its *next* refresh, half an
        // hour later, on a different screen. The admin SPA hit exactly this.
        final base = FakeTransport((spec, index) async {
          if (spec.path == refreshPath) {
            expect(
              (spec.body! as Map<String, dynamic>)['refresh'],
              'refresh-0',
            );
            return <String, dynamic>{
              'access': 'access-1',
              'refresh': 'refresh-1',
            };
          }
          if (index == 0) throwApi(401, null);
          return <String, dynamic>{'ok': true};
        });

        final transport = AuthRefreshTransport(base, hooksFor(store));
        await transport.send(
          const RequestSpec(method: HttpMethod.get, path: '/api/proxies/'),
        );

        expect(await store.readRefreshToken(), 'refresh-1');
        expect(store.accessToken, 'access-1');
      },
    );

    test('concurrent 401s share ONE in-flight refresh', () async {
      // Four screens loading at once produce four simultaneous 401s. Four
      // refreshes would each rotate and blacklist the previous token, so
      // three lose the race and the session dies for no reason the user
      // could ever explain.
      final failed = <String>{};
      final refreshGate = Completer<void>();

      final base = FakeTransport((spec, index) async {
        if (spec.path == refreshPath) {
          await refreshGate.future;
          return <String, dynamic>{
            'access': 'access-1',
            'refresh': 'refresh-1',
          };
        }
        if (!failed.contains(spec.path)) {
          failed.add(spec.path);
          throwApi(401, null);
        }
        return <String, dynamic>{'path': spec.path};
      });

      final transport = AuthRefreshTransport(base, hooksFor(store));
      final pending = <Future<Object?>>[
        for (final path in <String>[
          '/api/proxies/',
          '/api/chains/',
          '/api/subscription/',
          '/api/content/announcements/',
        ])
          transport.send(RequestSpec(method: HttpMethod.get, path: path)),
      ];

      // Let all four reach the refresh before any of them completes.
      await Future<void>.delayed(Duration.zero);
      refreshGate.complete();
      await Future.wait(pending);

      expect(transport.refreshCallCount, 1);
      expect(base.countFor(refreshPath), 1);
      expect(sessionLostCount, 0);
    });

    test('a 401 on the retry is terminal and ends the session', () async {
      final base = FakeTransport((spec, index) async {
        if (spec.path == refreshPath) {
          return <String, dynamic>{
            'access': 'access-1',
            'refresh': 'refresh-1',
          };
        }
        throwApi(401, null);
      });

      final transport = AuthRefreshTransport(base, hooksFor(store));

      await expectLater(
        transport.send(
          const RequestSpec(method: HttpMethod.get, path: '/api/proxies/'),
        ),
        throwsA(isA<ApiException>()),
      );
      expect(base.countFor('/api/proxies/'), 2, reason: 'no retry loop');
      expect(base.countFor(refreshPath), 1);
      expect(sessionLostCount, 1);
    });

    test('a failed refresh ends the session without retrying', () async {
      final base = FakeTransport((spec, index) async {
        if (spec.path == refreshPath) {
          throwApi(
            401,
            errorEnvelope(
              code: 'token_not_valid',
              status: 401,
              detail: 'Token is blacklisted',
            ),
          );
        }
        throwApi(401, null);
      });

      final transport = AuthRefreshTransport(base, hooksFor(store));
      await expectLater(
        transport.send(
          const RequestSpec(method: HttpMethod.get, path: '/api/proxies/'),
        ),
        throwsA(isA<ApiException>()),
      );
      expect(base.countFor('/api/proxies/'), 1);
      expect(sessionLostCount, 1);
    });

    test('403 is never refreshed — the token is fine, the plan is not', () async {
      final base = FakeTransport((spec, index) async {
        throwApi(
          403,
          errorEnvelope(
            code: 'proxy_not_entitled',
            status: 403,
            detail: <String, dynamic>{
              'message': 'One or more proxies are not included in your plan.',
              'proxy_ids': <int>[11],
            },
          ),
        );
      });

      final transport = AuthRefreshTransport(base, hooksFor(store));
      await expectLater(
        transport.send(
          const RequestSpec(method: HttpMethod.post, path: '/api/chains/'),
        ),
        throwsA(
          isA<ApiException>().having(
            (e) => e.code,
            'code',
            'proxy_not_entitled',
          ),
        ),
      );
      expect(base.countFor(refreshPath), 0);
      expect(sessionLostCount, 0);
    });

    test('with no stored refresh token the session ends immediately', () async {
      await store.clear();
      final base = FakeTransport((spec, index) async => throwApi(401, null));
      final transport = AuthRefreshTransport(base, hooksFor(store));

      await expectLater(
        transport.send(
          const RequestSpec(method: HttpMethod.get, path: '/api/proxies/'),
        ),
        throwsA(isA<ApiException>()),
      );
      expect(base.countFor(refreshPath), 0);
      expect(sessionLostCount, 1);
    });
  });
}
