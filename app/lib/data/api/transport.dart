import 'dart:async';

import 'package:flutter/foundation.dart';

import 'api_failure.dart';

/// HTTP verbs this client uses. An enum rather than a string so a typo is a
/// compile error.
enum HttpMethod { get, post, patch, put, delete }

extension HttpMethodName on HttpMethod {
  String get wire => switch (this) {
    HttpMethod.get => 'GET',
    HttpMethod.post => 'POST',
    HttpMethod.patch => 'PATCH',
    HttpMethod.put => 'PUT',
    HttpMethod.delete => 'DELETE',
  };
}

/// One request, described rather than performed.
///
/// Mirrors `admin/src/api/transport.ts` so both clients speak the same
/// contract against the same backend. Divergences are Dart-shaped only.
@immutable
class RequestSpec {
  const RequestSpec({
    required this.method,
    required this.path,
    this.query,
    this.body,
    this.auth = true,
    this.absoluteUrl,
  });

  final HttpMethod method;

  /// Path beginning with `/api/`. Ignored when [absoluteUrl] is set.
  final String path;

  final Map<String, Object?>? query;

  /// JSON request body. Encoded by the transport, never pre-encoded here.
  final Object? body;

  /// Attach the bearer token. The auth endpoints opt out.
  final bool auth;

  /// Escape hatch for a URL the server handed us — D18's `credentials_url`.
  /// Still goes through the same transport, so refresh-on-401, timeouts and
  /// the error envelope all apply.
  final String? absoluteUrl;

  RequestSpec copyWith({String? absoluteUrl}) => RequestSpec(
    method: method,
    path: path,
    query: query,
    body: body,
    auth: auth,
    absoluteUrl: absoluteUrl ?? this.absoluteUrl,
  );
}

/// The seam between the app and the network.
///
/// `HttpTransport` talks to Django; a fake answers from memory against the
/// same contract. Swapping them is a constructor argument — no call site is
/// aware of the difference.
abstract class Transport {
  /// Returns the decoded JSON body, or null for 204.
  ///
  /// Throws an [ApiFailure] subclass and nothing else: a caller never has to
  /// catch `SocketException` or `FormatException`.
  Future<Object?> send(RequestSpec spec);

  /// Releases any underlying client.
  void close();
}

/// Encodes a query map, dropping nulls and empty strings and joining lists
/// with commas (django-filter's own convention).
String buildQueryString(Map<String, Object?>? query) {
  if (query == null || query.isEmpty) return '';
  final parts = <String>[];
  query.forEach((key, value) {
    if (value == null) return;
    if (value is Iterable) {
      final joined = value.map((e) => '$e').where((e) => e.isNotEmpty).join(',');
      if (joined.isEmpty) return;
      parts.add(
        '${Uri.encodeQueryComponent(key)}=${Uri.encodeQueryComponent(joined)}',
      );
      return;
    }
    final text = '$value';
    if (text.isEmpty) return;
    parts.add(
      '${Uri.encodeQueryComponent(key)}=${Uri.encodeQueryComponent(text)}',
    );
  });
  return parts.isEmpty ? '' : '?${parts.join('&')}';
}

/// What a successful refresh hands back.
///
/// ⚠️ **Both halves matter.** `SIMPLE_JWT.ROTATE_REFRESH_TOKENS` and
/// `BLACKLIST_AFTER_ROTATION` are both on, so the refresh token that was
/// just submitted is blacklisted the moment the server answers. A client
/// that stores only `access` keeps working until its *next* refresh —
/// typically 30 minutes later, on a different screen — and is then signed
/// out with no obvious cause. The admin SPA hit exactly this; see
/// `admin/src/api/transport.ts` and `Project/memory.md` "Contract drift".
@immutable
class TokenPair {
  const TokenPair({required this.access, required this.refresh});

  final String access;
  final String refresh;

  factory TokenPair.fromJson(Map<String, dynamic> json) => TokenPair(
    access: json['access'] as String,
    // Defensive: if a future backend stops rotating, keeping the old token
    // is correct. Today it always sends a new one.
    refresh: json['refresh'] as String? ?? '',
  );

  /// Never put a credential in a log line, not even truncated.
  @override
  String toString() => 'TokenPair(access: <redacted>, refresh: <redacted>)';
}

/// Hooks the refresh wrapper needs from whatever owns the session.
class AuthRefreshHooks {
  const AuthRefreshHooks({
    required this.readRefreshToken,
    required this.storeTokens,
    required this.onSessionLost,
  });

  final Future<String?> Function() readRefreshToken;

  /// Must persist BOTH tokens before anything else can use the old one.
  final Future<void> Function(TokenPair tokens) storeTokens;

  /// Called once the session is unrecoverable. Clears state and returns the
  /// user to the sign-in screen.
  final Future<void> Function() onSessionLost;
}

/// Wraps a transport with refresh-on-401.
///
/// Three properties the naive version gets wrong:
///
/// 1. **Exactly one retry.** A 401 on the retry is terminal; looping would
///    burn refresh tokens against a server that has already said no.
/// 2. **One in-flight refresh.** Four screens loading at once produce four
///    simultaneous 401s. Four refreshes would each rotate the token and
///    blacklist the previous one, so three of them lose the race and the
///    session dies — the failure mode looks random and is miserable to
///    debug. They all await the same future instead.
/// 3. **403 is not refreshable.** The token is fine; the plan is not.
///    Refreshing it changes nothing and costs a rotation.
class AuthRefreshTransport implements Transport {
  AuthRefreshTransport(
    this._base,
    this._hooks, {
    this._refreshPath = '/api/auth/refresh/',
  });

  final Transport _base;
  final AuthRefreshHooks _hooks;
  final String _refreshPath;

  Future<TokenPair>? _inFlight;

  /// Visible for tests: how many times the refresh endpoint was actually
  /// called. Concurrent 401s must leave this at 1.
  @visibleForTesting
  int refreshCallCount = 0;

  Future<TokenPair> _refreshOnce() {
    final existing = _inFlight;
    if (existing != null) return existing;

    final future = _performRefresh();
    _inFlight = future;
    // The slot is cleared by the awaiting wrapper rather than by a detached
    // `whenComplete`. A detached continuation on a future that rejects is a
    // second, unhandled rejection — it does not change the caller's
    // behaviour, but it surfaces as an uncaught async error and fails tests
    // for a reason that has nothing to do with the code under test.
    return _awaitAndClear(future);
  }

  Future<TokenPair> _awaitAndClear(Future<TokenPair> future) async {
    try {
      return await future;
    } finally {
      if (identical(_inFlight, future)) _inFlight = null;
    }
  }

  Future<TokenPair> _performRefresh() async {
    final refresh = await _hooks.readRefreshToken();
    if (refresh == null || refresh.isEmpty) {
      throw ApiException(
        status: 401,
        code: ApiErrorCode.noRefreshToken,
        detail: 'Your session has expired. Sign in again.',
      );
    }
    refreshCallCount++;
    final body = await _base.send(
      RequestSpec(
        method: HttpMethod.post,
        path: _refreshPath,
        body: <String, dynamic>{'refresh': refresh},
        auth: false,
      ),
    );
    if (body is! Map<String, dynamic>) {
      throw const MalformedResponseFailure(
        'The server did not return a usable session.',
      );
    }
    final pair = TokenPair.fromJson(body);
    // Rotation: the token we just sent is blacklisted. Store the
    // replacement before any other request can read the old one.
    await _hooks.storeTokens(pair);
    return pair;
  }

  @override
  Future<Object?> send(RequestSpec spec) async {
    try {
      return await _base.send(spec);
    } on ApiException catch (error) {
      final refreshable =
          spec.auth && error.isUnauthorized && spec.path != _refreshPath;
      if (!refreshable) rethrow;

      try {
        await _refreshOnce();
      } on Object {
        await _hooks.onSessionLost();
        rethrow;
      }

      // The single permitted retry.
      try {
        return await _base.send(spec);
      } on ApiException catch (retryError) {
        if (retryError.isUnauthorized) await _hooks.onSessionLost();
        rethrow;
      }
    }
  }

  @override
  void close() => _base.close();
}
