import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import '../../core/net/host_pin.dart';
import 'api_config.dart';
import 'api_failure.dart';
import 'pinned_http_client.dart';
import 'transport.dart';

/// Talks to Django over HTTP.
///
/// Everything that can go wrong on the wire is converted into an
/// [ApiFailure] here, so nothing above this file ever catches a
/// `SocketException`, a `FormatException` or an `http.ClientException`.
class HttpTransport implements Transport {
  /// [hostPin] is P26's mitigation: while a tunnel is up, the API host is
  /// dialled at the address resolved before the TUN existed, because our own
  /// DNS cannot be kept out of the chain — `netd` resolves as root, so the
  /// UID the `process` rule matches is not on those packets. See
  /// `core/net/host_pin.dart`.
  ///
  /// Null, or an unpinned [HostPin], behaves exactly as the plain client
  /// did: every host is resolved normally.
  HttpTransport({
    required this.config,
    required this.readAccessToken,
    http.Client? client,
    HostPin? hostPin,
  }) : _client = client ?? createPinnedHttpClient(hostPin ?? HostPin()),
       _ownsClient = client == null;

  final ApiConfig config;

  /// Reads the in-memory access token. A function rather than a value so the
  /// transport always sees the token the refresh wrapper just stored.
  final String? Function() readAccessToken;

  final http.Client _client;
  final bool _ownsClient;

  @override
  Future<Object?> send(RequestSpec spec) async {
    final uri = _uriFor(spec);

    final headers = <String, String>{'Accept': 'application/json'};
    if (spec.auth) {
      final token = readAccessToken();
      if (token != null && token.isNotEmpty) {
        headers['Authorization'] = 'Bearer $token';
      }
    }

    final request = http.Request(spec.method.wire, uri);
    request.headers.addAll(headers);
    if (spec.body != null) {
      request.headers['Content-Type'] = 'application/json; charset=utf-8';
      request.body = jsonEncode(spec.body);
    }

    http.StreamedResponse streamed;
    try {
      streamed = await _client.send(request).timeout(config.timeout);
    } on TimeoutException {
      throw TimeoutFailure(
        'The server did not answer in time.',
        timeout: config.timeout,
      );
    } on SocketException catch (cause) {
      throw NetworkFailure(
        'Could not reach the server.',
        cause: cause,
        baseUrl: config.baseUrl,
      );
    } on http.ClientException catch (cause) {
      throw NetworkFailure(
        'Could not reach the server.',
        cause: cause,
        baseUrl: config.baseUrl,
      );
    } on HandshakeException catch (cause) {
      throw NetworkFailure(
        'The secure connection to the server failed.',
        cause: cause,
        baseUrl: config.baseUrl,
      );
    }

    final http.Response response;
    try {
      response = await http.Response.fromStream(
        streamed,
      ).timeout(config.timeout);
    } on TimeoutException {
      throw TimeoutFailure(
        'The server answered but the response never finished.',
        timeout: config.timeout,
      );
    }

    // 204 on delete.
    if (response.statusCode == 204 || response.bodyBytes.isEmpty) {
      if (response.statusCode >= 400) {
        throw apiExceptionFrom(
          response.statusCode,
          null,
          retryAfter: _retryAfter(response.headers),
        );
      }
      return null;
    }

    final text = utf8.decode(response.bodyBytes, allowMalformed: true);
    final contentType = response.headers['content-type'] ?? '';
    final Object? body;
    if (contentType.contains('application/json')) {
      try {
        body = jsonDecode(text);
      } on FormatException {
        if (response.statusCode >= 400) {
          throw apiExceptionFrom(
            response.statusCode,
            text,
            retryAfter: _retryAfter(response.headers),
          );
        }
        throw MalformedResponseFailure(
          'The server sent a response this app could not read.',
          snippet: _snippet(text),
        );
      }
    } else {
      body = text;
    }

    if (response.statusCode >= 400) {
      throw apiExceptionFrom(
        response.statusCode,
        body,
        retryAfter: _retryAfter(response.headers),
      );
    }

    // A 2xx that is not JSON means something is in front of Django — a
    // captive portal, a proxy error page, the Vite dev server. Saying so is
    // far more useful than a cast error three layers up.
    if (!contentType.contains('application/json')) {
      throw MalformedResponseFailure(
        'The server sent ${contentType.isEmpty ? 'an untyped response' : contentType} '
        'where JSON was expected.',
        snippet: _snippet(text),
      );
    }
    return body;
  }

  Uri _uriFor(RequestSpec spec) {
    final absolute = spec.absoluteUrl;
    if (absolute != null && absolute.isNotEmpty) {
      return Uri.parse('$absolute${buildQueryString(spec.query)}');
    }
    return Uri.parse('${config.baseUrl}${spec.path}${buildQueryString(spec.query)}');
  }

  static String? _snippet(String text) {
    final trimmed = text.trim();
    if (trimmed.isEmpty) return null;
    return trimmed.length <= 180 ? trimmed : '${trimmed.substring(0, 180)}…';
  }

  /// `Retry-After` is either seconds or an HTTP date. Both become a Duration.
  static Duration? _retryAfter(Map<String, String> headers) {
    final raw = headers['retry-after'];
    if (raw == null || raw.isEmpty) return null;
    final seconds = int.tryParse(raw.trim());
    if (seconds != null) return Duration(seconds: seconds);
    final when = DateTime.tryParse(raw);
    if (when == null) return null;
    final delta = when.difference(DateTime.now());
    return delta.isNegative ? Duration.zero : delta;
  }

  @override
  void close() {
    if (_ownsClient) _client.close();
  }
}
