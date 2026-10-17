import 'package:flutter_test/flutter_test.dart';
import 'package:proxy_crack/data/api/api_client.dart';
import 'package:proxy_crack/data/api/api_config.dart';
import 'package:proxy_crack/data/api/api_failure.dart';
import 'package:proxy_crack/data/api/transport.dart';
import 'package:proxy_crack/data/models/plan.dart';
import 'package:proxy_crack/data/models/proxy.dart';
import 'package:proxy_crack/data/models/proxy_credentials.dart';

import 'fake_transport.dart';

const ApiConfig config = ApiConfig(baseUrl: 'http://10.0.2.2:8000');

ApiClient clientWith(FakeTransport transport) =>
    ApiClient(transport: transport, config: config);

void main() {
  group('error envelope → typed failure', () {
    test('chain_length_exceeded keeps its numbers as numbers', () async {
      // DRF stringifies every leaf of an exception detail by default; the
      // backend deliberately keeps the dict so the client can compare
      // against the limits rather than parse them out of a sentence.
      final client = clientWith(
        FakeTransport(
          (spec, index) => throwApi(
            403,
            errorEnvelope(
              code: 'chain_length_exceeded',
              status: 403,
              detail: <String, dynamic>{
                'message': 'Chain is longer than your plan allows.',
                'requested_length': 3,
                'max_chain_length': 1,
                'plan': 'free',
              },
            ),
          ),
        ),
      );

      try {
        await client.createChain(name: 'x', proxyIds: <int>[1, 2, 3]);
        fail('should have thrown');
      } on ApiException catch (error) {
        expect(error.hasCode(ApiErrorCode.chainLengthExceeded), isTrue);
        expect(error.status, 403);
        expect(error.isForbidden, isTrue);
        expect(error.detailValue('max_chain_length'), 1);
        expect(error.detailValue('requested_length'), 3);
        expect(error.message, 'Chain is longer than your plan allows.');
        // A structured entitlement detail is not a form error: rendering
        // "max_chain_length: 1" under a text input would be nonsense.
        expect(error.fieldErrors, isEmpty);
      }
    });

    test('chain_unresolvable exposes both levels of code', () async {
      final client = clientWith(
        FakeTransport(
          (spec, index) => throwApi(
            409,
            errorEnvelope(
              code: 'chain_unresolvable',
              status: 409,
              detail: <String, dynamic>{
                'message': 'No healthy proxy satisfies hop 2.',
                'code': 'hop_unsatisfied',
                'position': 1,
              },
            ),
          ),
        ),
      );

      try {
        await client.resolveChain(7);
        fail('should have thrown');
      } on ApiException catch (error) {
        expect(error.hasCode(ApiErrorCode.chainUnresolvable), isTrue);
        expect(error.isConflict, isTrue);
        expect(error.resolutionCode, ChainResolutionCode.hopUnsatisfied);
        expect(error.failingPosition, 1);
      }
    });

    test('proxy_not_entitled carries the offending ids', () async {
      final client = clientWith(
        FakeTransport(
          (spec, index) => throwApi(
            403,
            errorEnvelope(
              code: 'proxy_not_entitled',
              status: 403,
              detail: <String, dynamic>{
                'message': 'One or more proxies are not included in your plan.',
                'proxy_ids': <int>[11],
                'allowed_proxy_tiers': <String>['free'],
                'plan': 'free',
              },
            ),
          ),
        ),
      );

      try {
        await client.previewDiversity(<int>[11]);
        fail('should have thrown');
      } on ApiException catch (error) {
        expect(error.hasCode(ApiErrorCode.proxyNotEntitled), isTrue);
        expect(error.detailValue('proxy_ids'), <int>[11]);
      }
    });

    test('429 surfaces the wait rather than a bare status code', () async {
      final client = clientWith(
        FakeTransport(
          (spec, index) => throwApi(
            429,
            errorEnvelope(
              code: 'throttled',
              status: 429,
              detail: 'Request was throttled. Expected available in 42 seconds.',
            ),
            retryAfter: const Duration(seconds: 42),
          ),
        ),
      );

      try {
        await client.fetchCredentials(proxyId: 11);
        fail('should have thrown');
      } on ApiException catch (error) {
        expect(error.isThrottled, isTrue);
        expect(error.retryAfter, const Duration(seconds: 42));
        expect(error.message, contains('42 seconds'));
        expect(error.isRetryable, isTrue);
      }
    });

    test('a 400 field map becomes inline form errors', () async {
      final client = clientWith(
        FakeTransport(
          (spec, index) => throwApi(400, <String, dynamic>{
            'email': <String>['An account with this email exists.'],
            'password': 'This password is too common.',
          }),
        ),
      );

      try {
        await client.register(
          email: 'a@b.c',
          password: 'password',
          passwordConfirm: 'password',
        );
        fail('should have thrown');
      } on ApiException catch (error) {
        expect(
          error.fieldErrors['email'],
          'An account with this email exists.',
        );
        expect(error.fieldErrors['password'], 'This password is too common.');
      }
    });
  });

  group('credentials', () {
    test(
      'an absolute credentials_url is re-based onto the configured host',
      () async {
        // `request.build_absolute_uri` names whatever Host header reached
        // Django. Behind a reverse proxy — or anywhere the device did not
        // dial that name — the origin is wrong and only the path is
        // authoritative. From an emulator, 127.0.0.1 is the emulator.
        late String requestedPath;
        final transport = FakeTransport((spec, index) {
          requestedPath = spec.path;
          return <String, dynamic>{
            'id': 11,
            'username': 'u',
            'password': 'p',
            'ss_method': '',
          };
        });

        final credentials = await clientWith(transport).fetchCredentials(
          proxyId: 11,
          url: 'http://127.0.0.1:8000/api/proxies/11/',
        );

        expect(requestedPath, '/api/proxies/11/');
        expect(credentials.proxyId, 11);
      },
    );

    test('credentials never appear in toString', () {
      // The paid credentials are the product. A log line, a crash report or
      // an interpolated error message must not be able to carry them.
      const secret = 'not-a-real-credential-9f3b';
      const credentials = ProxyCredentials(
        proxyId: 11,
        username: secret,
        password: secret,
        ssMethod: 'aes-256-gcm',
      );
      expect(credentials.toString(), isNot(contains(secret)));
      expect(credentials.toString(), contains('redacted'));

      const chain = ChainCredentials(<int, ProxyCredentials>{
        11: credentials,
      });
      expect(chain.toString(), isNot(contains(secret)));
    });
  });

  group('pagination', () {
    test('fetchAllProxies walks every page', () async {
      final transport = FakeTransport((spec, index) {
        final page = int.parse('${spec.query!['page']}');
        return <String, dynamic>{
          'count': 3,
          'next': page < 3 ? 'http://x/api/proxies/?page=${page + 1}' : null,
          'previous': null,
          'results': <Map<String, dynamic>>[
            <String, dynamic>{
              'id': page,
              'protocol': 'http',
              'host': 'h$page',
              'port': 8080,
              'tier': 'premium',
            },
          ],
        };
      });

      final all = await clientWith(transport).fetchAllProxies();
      expect(all.map((p) => p.id), <int>[1, 2, 3]);
      expect(transport.countFor('/api/proxies/'), 3);
    });

    test('a bare list is accepted where pagination was expected', () async {
      // `/api/content/announcements/` returns an unpaginated list when the
      // queryset fits on one page.
      final transport = FakeTransport(
        (spec, index) => <Map<String, dynamic>>[
          <String, dynamic>{
            'id': 1,
            'key': 'a',
            'kind': 'announcement',
            'title': 'T',
            'body': 'B',
            'priority': 1,
          },
        ],
      );
      final items = await clientWith(transport).fetchAnnouncements();
      expect(items, hasLength(1));
      expect(items.first.title, 'T');
    });
  });

  group('model parsing against the real wire shapes', () {
    test('Plan.price is a decimal STRING and becomes exact cents', () {
      // DRF serialises DecimalField as a string. Parsing to a double and
      // multiplying by 100 reintroduces binary-float rounding on money.
      final plan = Plan.fromJson(<String, dynamic>{
        'id': 2,
        'code': 'pro-monthly',
        'name': 'Pro Monthly',
        'description': 'Three-hop chains.',
        'price': '7.99',
        'currency': 'USD',
        'duration_days': 30,
        'max_chain_length': 3,
        'max_devices': 3,
        'bandwidth_limit_bytes': 0,
        'max_proxy_tier': 'premium',
        'is_free_tier': false,
        'play_product_id': null,
      });

      expect(plan.priceCents, 799);
      expect(plan.priceLabel, r'$7.99');
      expect(plan.code, 'pro-monthly');
      expect(plan.tagline, 'Three-hop chains.');
      expect(plan.isUnmetered, isTrue);
    });

    test('a null latency is never rendered as a fast proxy', () {
      // `latency_ms`, `success_rate` and `latency_p95_ms` are all nullable
      // on the server and null means "no measurement", not "zero".
      final unprobed = Proxy.fromJson(<String, dynamic>{
        'id': 1,
        'protocol': 'http',
        'host': 'h',
        'port': 8080,
        'tier': 'premium',
        'is_alive': true,
        'latency_ms': null,
        'success_rate': null,
        'probe_count': 0,
        'latency_p95_ms': null,
        'quality_score': 50.0,
      });

      expect(unprobed.isUnprobed, isTrue);
      expect(unprobed.effectiveLatencyMs, isNull);
      expect(unprobed.latencyGrade, LatencyGrade.unknown);
      expect(unprobed.successPercent, isNull);
    });

    test('a proxy that failed 50 probes is NOT treated as unprobed', () {
      // Keying "unprobed" on a null p95 would re-admit the worst proxies in
      // the inventory under a flag promising "nothing known yet" (P15).
      final dead = Proxy.fromJson(<String, dynamic>{
        'id': 2,
        'protocol': 'socks5',
        'host': 'h',
        'port': 1080,
        'tier': 'free',
        'is_alive': false,
        'latency_ms': null,
        'success_rate': 0.0,
        'probe_count': 50,
        'consecutive_failures': 50,
        'latency_p95_ms': null,
        'quality_score': 0.0,
      });

      expect(dead.isUnprobed, isFalse);
      expect(dead.successPercent, 0);
      expect(dead.latencyGrade, LatencyGrade.dead);
    });

    test('the real proxy row shape parses, empty country included', () {
      // Taken from the dev database: `country` and `city` are blank, so
      // there is no two-letter code to show.
      final proxy = Proxy.fromJson(<String, dynamic>{
        'id': 11,
        'protocol': 'http',
        'protocol_display': 'HTTP CONNECT',
        'host': '203.0.113.9',
        'port': 6014,
        'country': '',
        'city': '',
        'tier': 'premium',
        'is_alive': true,
        'latency_ms': 391,
        'last_checked': '2026-10-17T09:00:00Z',
        'success_rate': 1.0,
        'probe_count': 6,
        'consecutive_failures': 0,
        'latency_p50_ms': 391,
        'latency_p95_ms': 391,
        'quality_score': 68.7665,
        'requires_credentials': true,
      });

      expect(proxy.countryCode, '--');
      expect(proxy.location, '203.0.113.9');
      expect(proxy.successPercent, 100);
      expect(proxy.qualityScore, closeTo(68.77, 0.01));
      expect(proxy.requiresCredentials, isTrue);
      expect(proxy.protocol.carriesUdp, isFalse);
    });
  });

  group('ApiConfig', () {
    test('trailing slashes never produce a double slash', () {
      expect(ApiConfig.normalise('http://10.0.2.2:8000/'), 'http://10.0.2.2:8000');
      expect(
        ApiConfig.normalise('http://10.0.2.2:8000///'),
        'http://10.0.2.2:8000',
      );
    });

    test('cleartext is detected so the app can say so', () {
      expect(const ApiConfig(baseUrl: 'http://x').isCleartext, isTrue);
      expect(const ApiConfig(baseUrl: 'https://x').isCleartext, isFalse);
    });
  });

  group('query building', () {
    test('nulls and empty strings are dropped, lists are comma-joined', () {
      final qs = buildQueryString(<String, Object?>{
        'page': 1,
        'country': null,
        'city': '',
        'tier': <String>['free', 'premium'],
        'is_alive': true,
      });
      expect(qs, '?page=1&tier=free%2Cpremium&is_alive=true');
    });
  });
}
