import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:proxy_crack/core/net/host_pin.dart';
import 'package:proxy_crack/data/api/pinned_http_client.dart';

/// P26: the app's own DNS cannot be kept out of the chain.
///
/// `netd` resolves on behalf of every app, as **root (UID 0)**, in its own
/// process. On API 29+ an app's `getaddrinfo` goes through it, so the DNS
/// packets that reach the tunnel carry netd's UID — not ours — and the
/// `process` routing rule, which matches our UID, cannot match them.
///
/// Exempting netd's UID would "fix" it by taking *every app's* DNS off the
/// tunnel: a device-wide DNS leak, and precisely the disclosure this product
/// exists to prevent. So the fix is to need no DNS at all while the tunnel is
/// up — resolve the API host before `establish()`, and dial that address for
/// the session.
///
/// The thing most likely to go wrong is not the pinning. It is TLS: dial an
/// address while the certificate is issued for a name and validation fails,
/// SNI picks the wrong vhost, and Django answers 400 `DisallowedHost` —
/// none of which is visible in dev, where the base URL is a bare IP over
/// cleartext. That is what most of this file is about.
void main() {
  // These tests use real sockets. No `TestWidgetsFlutterBinding` is
  // initialised here on purpose: it installs an `HttpOverrides` that
  // replaces every `HttpClient` with a mock, and the mock would make the
  // TLS and Host-header assertions below vacuously pass.
  setUpAll(() => HttpOverrides.global = null);

  // -------------------------------------------------------------------
  // 1. The no-op path: an address needs no lookup
  // -------------------------------------------------------------------

  group('hostToPin', () {
    test('an IPv4 base URL is a no-op', () {
      // Today's dev default. There is no name here, so there is nothing to
      // resolve and nothing to pin — the lookup would be a pointless round
      // trip that can only fail.
      expect(hostToPin('http://10.0.2.2:8000'), isNull);
      expect(hostToPin('http://127.0.0.1:8000'), isNull);
      expect(hostToPin('https://203.0.113.10'), isNull);
    });

    test('an IPv6 literal base URL is a no-op', () {
      expect(hostToPin('http://[::1]:8000'), isNull);
      expect(hostToPin('http://[2001:db8::1]:8000'), isNull);
    });

    test('a hostname base URL names the host to pin, lower-cased', () {
      expect(hostToPin('https://api.example.com'), 'api.example.com');
      expect(hostToPin('https://API.Example.COM:8443'), 'api.example.com');
      expect(hostToPin('http://dns-probe.example.com:8000'), 'dns-probe.example.com');
    });

    test('a base URL with no host is a no-op, not a crash', () {
      expect(hostToPin(''), isNull);
      expect(hostToPin('not a url'), isNull);
    });
  });

  // -------------------------------------------------------------------
  // 2. Which address, out of several
  // -------------------------------------------------------------------

  group('selectPinnedAddress', () {
    InternetAddress v4(String a) => InternetAddress(a, type: InternetAddressType.IPv4);
    InternetAddress v6(String a) => InternetAddress(a, type: InternetAddressType.IPv6);

    test('several A records always produce the same pin', () {
      // A bug that depends on which of four addresses was taken is a bug
      // nobody can reproduce. The order the resolver returns is not stable;
      // this is.
      final answer = <InternetAddress>[
        v4('203.0.113.40'),
        v4('203.0.113.7'),
        v4('198.51.100.9'),
        v4('203.0.113.200'),
      ];
      final first = selectPinnedAddress(answer);
      expect(first!.address, '198.51.100.9');
      expect(
        selectPinnedAddress(answer.reversed.toList())!.address,
        first.address,
      );
    });

    test('IPv4 is preferred over IPv6 whatever order they arrive in', () {
      expect(
        selectPinnedAddress(<InternetAddress>[
          v6('2001:db8::1'),
          v4('203.0.113.7'),
        ])!.address,
        '203.0.113.7',
      );
    });

    test('IPv6 is used only when there is no IPv4 at all', () {
      // Not silently preferred — only taken when it is the sole answer, as
      // it is on an IPv6-only carrier with DNS64. The pinned API socket is
      // dialled by our own process, which the `process` rule sends direct,
      // so it is carried by the physical interface rather than by a chain
      // whose DNS is v4-only.
      final chosen = selectPinnedAddress(<InternetAddress>[
        v6('2001:db8::20'),
        v6('2001:db8::2'),
      ]);
      expect(chosen!.type, InternetAddressType.IPv6);
      expect(chosen.address, '2001:db8::2');
    });

    test('an empty answer selects nothing', () {
      expect(selectPinnedAddress(const <InternetAddress>[]), isNull);
    });
  });

  // -------------------------------------------------------------------
  // 3. A failure to resolve must be loud and must happen early
  // -------------------------------------------------------------------

  group('resolveHostOnce', () {
    test('a resolver that fails becomes a user-facing refusal', () async {
      await expectLater(
        resolveHostOnce(
          'api.example.com',
          resolver: (_) => Future<List<InternetAddress>>.error(
            const SocketException('Failed host lookup'),
          ),
        ),
        throwsA(
          isA<HostResolutionException>()
              .having((e) => e.host, 'host', 'api.example.com')
              // Says what did not happen, so the user is not left wondering
              // whether they are connected.
              .having((e) => e.message, 'message', contains('not started')),
        ),
      );
    });

    test('an empty answer is a refusal, not a null pin', () async {
      await expectLater(
        resolveHostOnce(
          'api.example.com',
          resolver: (_) async => const <InternetAddress>[],
        ),
        throwsA(isA<HostResolutionException>()),
      );
    });

    test('a hung resolver is bounded', () async {
      // On the connect path. An unbounded lookup would be one more way for
      // the connect button to never come back.
      final stopwatch = Stopwatch()..start();
      await expectLater(
        resolveHostOnce(
          'api.example.com',
          resolver: (_) => Completer<List<InternetAddress>>().future,
          timeout: const Duration(milliseconds: 80),
        ),
        throwsA(isA<HostResolutionException>()),
      );
      stopwatch.stop();
      expect(stopwatch.elapsed, lessThan(const Duration(seconds: 2)));
    });
  });

  // -------------------------------------------------------------------
  // 4. The pin matches one host and nothing else
  // -------------------------------------------------------------------

  group('HostPin', () {
    test('it answers for the pinned host, case-insensitively, and no other',
        () {
      final pin = HostPin()
        ..pin('API.Example.com', InternetAddress('203.0.113.7'));

      expect(pin.isPinned, isTrue);
      expect(pin.addressFor('api.example.com')!.address, '203.0.113.7');
      expect(pin.addressFor('API.EXAMPLE.COM')!.address, '203.0.113.7');
      // Never a general redirect: everything else resolves normally.
      expect(pin.addressFor('example.com'), isNull);
      expect(pin.addressFor('evil.api.example.com'), isNull);
      expect(pin.addressFor('google.com'), isNull);
    });

    test('clear restores ordinary resolution', () {
      final pin = HostPin()..pin('api.example.com', InternetAddress('203.0.113.7'));
      pin.clear();
      expect(pin.isPinned, isFalse);
      expect(pin.addressFor('api.example.com'), isNull);
    });

    test('an unpinned pin never redirects anything', () {
      expect(HostPin().addressFor('api.example.com'), isNull);
    });
  });

  // -------------------------------------------------------------------
  // 5. ⚠️ The part that only breaks in production: TLS and Host
  // -------------------------------------------------------------------

  group('createPinnedHttpClient', () {
    test('the pinned address is dialled while the Host header keeps the name',
        () async {
      // The host below cannot resolve — `.invalid` is reserved by RFC 2606
      // precisely so that it never does. So the server receiving anything
      // at all is the proof that the pinned address was dialled, and the
      // request line proves the name survived the substitution.
      final server = await _RecordingServer.start();
      addTearDown(server.close);

      final pin = HostPin()
        ..pin('api.proxycrack.invalid', InternetAddress.loopbackIPv4);
      final client = createPinnedHttpClient(pin);
      addTearDown(client.close);

      final response = await client
          .get(Uri.parse('http://api.proxycrack.invalid:${server.port}/api/ping/'))
          .timeout(const Duration(seconds: 5));

      expect(response.statusCode, 200);

      // `dart:io` writes header names lower-cased on the wire.
      final request = (await server.firstRequest).toLowerCase();
      // Django's ALLOWED_HOSTS is checked against this header. Send the
      // address instead and every request is a 400 DisallowedHost before it
      // reaches a view.
      expect(
        request,
        contains('host: api.proxycrack.invalid:${server.port}'),
      );
      expect(request, contains('get /api/ping/ http/1.1'));
      expect(request, isNot(contains('host: 127.0.0.1')));
    });

    test('https carries the hostname in SNI, not the pinned address',
        () async {
      // The whole reason this is a connection factory and not a URL rewrite.
      // The ClientHello is sent in clear, so a plain TCP server can read the
      // server_name extension out of it without needing a certificate.
      final server = await _RecordingServer.start(closeOnFirstRead: true);
      addTearDown(server.close);

      final pin = HostPin()
        ..pin('api.proxycrack.invalid', InternetAddress.loopbackIPv4);
      final client = createPinnedHttpClient(pin);
      addTearDown(client.close);

      // There is no TLS server on the other end, so the handshake fails.
      // The ClientHello has already been sent by then, which is all this
      // needs.
      unawaited(
        client
            .get(Uri.parse('https://api.proxycrack.invalid:${server.port}/api/ping/'))
            .then<void>((_) {}, onError: (Object _) {}),
      );

      final hello = await server.firstRequestBytes
          .timeout(const Duration(seconds: 5));

      expect(
        _containsAscii(hello, 'api.proxycrack.invalid'),
        isTrue,
        reason: 'SNI did not carry the hostname, so the server would answer '
            'with the wrong certificate and validation would fail',
      );
      expect(
        _containsAscii(hello, '127.0.0.1'),
        isFalse,
        reason: 'the pinned address leaked into the handshake',
      );
      // A TLS record, not a cleartext GET: proof the factory secured the
      // socket itself. dart:io does *not* do it for a socket that came from
      // a connectionFactory, and a plain socket here would send the request
      // in clear to a TLS port.
      expect(hello.first, 0x16, reason: 'not a TLS handshake record');
      expect(_containsAscii(hello, 'GET /api/ping/'), isFalse);
    });

    test('a host that is not pinned still resolves normally', () async {
      final server = await _RecordingServer.start();
      addTearDown(server.close);

      final pin = HostPin()
        ..pin('api.proxycrack.invalid', InternetAddress.loopbackIPv4);
      final client = createPinnedHttpClient(pin);
      addTearDown(client.close);

      final response = await client
          .get(Uri.parse('http://localhost:${server.port}/api/ping/'))
          .timeout(const Duration(seconds: 5));

      expect(response.statusCode, 200);
      expect(
        (await server.firstRequest).toLowerCase(),
        contains('host: localhost:'),
      );
    });

    test('an unpinned client behaves exactly as the plain one did', () async {
      final server = await _RecordingServer.start();
      addTearDown(server.close);

      final client = createPinnedHttpClient(HostPin());
      addTearDown(client.close);

      final response = await client
          .get(Uri.parse('http://127.0.0.1:${server.port}/api/ping/'))
          .timeout(const Duration(seconds: 5));
      expect(response.statusCode, 200);
    });
  });
}

bool _containsAscii(List<int> haystack, String needle) {
  final needleBytes = ascii.encode(needle);
  if (needleBytes.isEmpty || haystack.length < needleBytes.length) return false;
  for (var i = 0; i <= haystack.length - needleBytes.length; i++) {
    var match = true;
    for (var j = 0; j < needleBytes.length; j++) {
      if (haystack[i + j] != needleBytes[j]) {
        match = false;
        break;
      }
    }
    if (match) return true;
  }
  return false;
}

/// A loopback server that records the first thing written to it.
///
/// For the cleartext tests it answers a minimal 200; for the TLS test it
/// hangs up, because all that is needed there is the ClientHello.
class _RecordingServer {
  _RecordingServer._(this._server, this._closeOnFirstRead) {
    _server.listen((Socket socket) {
      final buffer = <int>[];
      socket.listen(
        (data) {
          buffer.addAll(data);
          if (_closeOnFirstRead) {
            if (!_bytes.isCompleted) _bytes.complete(List<int>.of(buffer));
            socket.destroy();
            return;
          }
          if (!_isRequestComplete(buffer)) return;
          if (!_bytes.isCompleted) _bytes.complete(List<int>.of(buffer));
          socket.add(
            ascii.encode(
              'HTTP/1.1 200 OK\r\n'
              'Content-Type: application/json\r\n'
              'Content-Length: 2\r\n'
              '\r\n'
              '{}',
            ),
          );
          socket.flush().then<void>(
                (_) => socket.close(),
                onError: (Object _) {},
              );
        },
        onError: (Object _) {},
        cancelOnError: true,
      );
    });
  }

  static Future<_RecordingServer> start({bool closeOnFirstRead = false}) async {
    final server = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
    return _RecordingServer._(server, closeOnFirstRead);
  }

  final ServerSocket _server;
  final bool _closeOnFirstRead;
  final Completer<List<int>> _bytes = Completer<List<int>>();

  int get port => _server.port;

  Future<List<int>> get firstRequestBytes => _bytes.future;

  Future<String> get firstRequest async =>
      String.fromCharCodes(await _bytes.future);

  Future<void> close() => _server.close();

  static bool _isRequestComplete(List<int> buffer) {
    for (var i = 0; i + 3 < buffer.length; i++) {
      if (buffer[i] == 0x0D &&
          buffer[i + 1] == 0x0A &&
          buffer[i + 2] == 0x0D &&
          buffer[i + 3] == 0x0A) {
        return true;
      }
    }
    return false;
  }
}
