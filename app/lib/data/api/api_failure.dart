import 'package:flutter/foundation.dart';

/// Error codes the backend puts in `error.code`.
///
/// Screens branch on these constants rather than on message text. A string
/// match against a human-readable sentence breaks the first time someone
/// improves the copy, and the copy is exactly the part that is expected to
/// change.
abstract final class ApiErrorCode {
  // --- entitlement (403) -------------------------------------------------
  static const String chainLengthExceeded = 'chain_length_exceeded';
  static const String proxyNotEntitled = 'proxy_not_entitled';

  // --- resolution (409) --------------------------------------------------
  static const String chainUnresolvable = 'chain_unresolvable';

  // --- generic DRF -------------------------------------------------------
  static const String throttled = 'throttled';
  static const String notAuthenticated = 'not_authenticated';
  static const String authenticationFailed = 'authentication_failed';
  static const String permissionDenied = 'permission_denied';
  static const String notFound = 'not_found';
  static const String invalid = 'invalid';

  /// Synthesised client-side when the session cannot be recovered.
  static const String noRefreshToken = 'no_refresh_token';

  /// Fallback when the server did not name one.
  static const String unknown = 'error';
}

/// Inner `error.detail.code` values carried by a 409 `chain_unresolvable`.
///
/// Two levels of code is deliberate on the backend's side: the client's
/// first question is "can I connect", and only the screen that explains the
/// failure needs the second.
abstract final class ChainResolutionCode {
  static const String chainEmpty = 'chain_empty';
  static const String chainLengthExceeded = 'chain_length_exceeded';
  static const String hopDuplicateProxy = 'hop_duplicate_proxy';
  static const String hopProxyDisabled = 'hop_proxy_disabled';
  static const String hopProxyNotEntitled = 'hop_proxy_not_entitled';
  static const String hopProxyDead = 'hop_proxy_dead';
  static const String hopUnsatisfied = 'hop_unsatisfied';
  static const String hopUnsatisfiedUnprobed = 'hop_unsatisfied_unprobed';
}

/// Anything that stopped a call from returning data.
///
/// Sealed so a `switch` over it is exhaustive and a new failure kind cannot
/// be silently ignored by an existing screen.
@immutable
sealed class ApiFailure implements Exception {
  const ApiFailure(this.message);

  /// One user-facing sentence. Already actionable — never a stack trace.
  final String message;

  /// Second line with specifics, or null.
  String? get detailLine => null;

  /// True when retrying the same call right now might work.
  bool get isRetryable => true;

  /// True when the device could not reach the server at all, as opposed to
  /// the server answering with a refusal. Drives the offline state.
  bool get isOffline => false;

  @override
  String toString() => '$runtimeType: $message';
}

/// The request never reached Django: no route, DNS failure, refused socket,
/// aeroplane mode, or the dev server simply not running.
final class NetworkFailure extends ApiFailure {
  const NetworkFailure(super.message, {this.cause, this.baseUrl});

  final Object? cause;
  final String? baseUrl;

  @override
  bool get isOffline => true;

  @override
  String? get detailLine => baseUrl == null
      ? null
      : 'Tried $baseUrl. Check the backend is running and that the base URL '
            'is right for this device.';
}

/// The server accepted the connection but did not answer in time.
final class TimeoutFailure extends ApiFailure {
  const TimeoutFailure(super.message, {this.timeout});

  final Duration? timeout;

  @override
  bool get isOffline => true;

  @override
  String? get detailLine => timeout == null
      ? null
      : 'No answer within ${timeout!.inSeconds} seconds.';
}

/// The body was not the JSON the contract promises.
final class MalformedResponseFailure extends ApiFailure {
  const MalformedResponseFailure(super.message, {this.snippet});

  /// A short, truncated excerpt. Never the whole body — it may be an HTML
  /// error page thousands of lines long.
  final String? snippet;

  @override
  bool get isRetryable => false;

  @override
  String? get detailLine => snippet;
}

/// A structured refusal from the API, carrying the
/// `{"error": {"code", "status", "detail"}}` envelope intact.
///
/// `detail` is kept raw because the backend deliberately puts *numbers* in
/// it — `max_chain_length`, `position`, `proxy_ids` — that a screen compares
/// against rather than prints.
final class ApiException extends ApiFailure {
  ApiException({
    required this.status,
    required this.code,
    required this.detail,
    this.extra = const <String, dynamic>{},
    this.retryAfter,
    String? message,
  }) : super(message ?? _summarise(status, code, detail, retryAfter));

  final int status;
  final String code;

  /// A String, a List, or a Map — DRF emits all three shapes.
  final Object? detail;

  /// Members of the error object beyond `code`/`status`/`detail`.
  final Map<String, dynamic> extra;

  /// Parsed from the `Retry-After` header on a 429.
  final Duration? retryAfter;

  bool hasCode(String candidate) => code == candidate;

  /// The second-level code inside a `chain_unresolvable` detail, or null.
  String? get resolutionCode => detailValue('code') as String?;

  /// The failing hop position inside a `chain_unresolvable` detail, or null.
  int? get failingPosition {
    final value = detailValue('position');
    return value is int ? value : null;
  }

  /// A named member of a map-shaped `detail`.
  Object? detailValue(String key) {
    final d = detail;
    return d is Map<String, dynamic> ? d[key] : null;
  }

  /// `field -> message`, for inline form validation. Empty when the failure
  /// was not field-shaped.
  Map<String, String> get fieldErrors {
    final d = detail;
    if (d is! Map<String, dynamic>) return const <String, String>{};
    // A structured entitlement detail carries `message` + numbers, not
    // fields. Treating it as a field map would print "max_chain_length: 1"
    // under a text input.
    if (d.containsKey('message')) return const <String, String>{};
    final out = <String, String>{};
    d.forEach((key, value) {
      if (value is String) {
        out[key] = value;
      } else if (value is List) {
        final parts = value.whereType<String>().toList();
        if (parts.isNotEmpty) out[key] = parts.join(' ');
      }
    });
    return out;
  }

  /// 401 is refreshable; everything else is not. Used by the auth transport.
  bool get isUnauthorized => status == 401;

  /// A 403 can never be fixed by minting a new access token — the token is
  /// valid, the plan is not. Retrying one burns a refresh token for nothing.
  bool get isForbidden => status == 403;

  bool get isThrottled => status == 429;

  bool get isConflict => status == 409;

  @override
  bool get isRetryable => status >= 500 || status == 429;

  @override
  String? get detailLine {
    final d = detail;
    if (d is Map<String, dynamic>) {
      final inner = d['message'];
      if (inner is String && inner != message) return inner;
    }
    return null;
  }

  @override
  String toString() => 'ApiException($status $code): $message';
}

String _summarise(
  int status,
  String code,
  Object? detail,
  Duration? retryAfter,
) {
  if (status == 429) {
    final wait = _formatWait(retryAfter);
    if (detail is String && detail.trim().isNotEmpty) {
      return wait == null ? detail : '$detail Try again in $wait.';
    }
    return wait == null
        ? 'Too many requests. Try again shortly.'
        : 'Too many requests. Try again in $wait.';
  }
  if (detail is String && detail.trim().isNotEmpty) return detail;
  if (detail is List) {
    final parts = detail.whereType<String>().toList();
    if (parts.isNotEmpty) return parts.join(' ');
  }
  if (detail is Map<String, dynamic>) {
    final message = detail['message'];
    if (message is String && message.trim().isNotEmpty) return message;
    final parts = <String>[];
    detail.forEach((key, value) {
      if (value is String) {
        parts.add('$key: $value');
      } else if (value is List) {
        final inner = value.whereType<String>();
        if (inner.isNotEmpty) parts.add('$key: ${inner.join(' ')}');
      }
    });
    if (parts.isNotEmpty) return parts.join(' · ');
  }
  return 'The server refused the request ($status).';
}

String? _formatWait(Duration? d) {
  if (d == null || d.inSeconds <= 0) return null;
  final seconds = d.inSeconds;
  if (seconds < 90) return '$seconds seconds';
  final minutes = (seconds / 60).round();
  if (minutes < 90) return '$minutes minutes';
  return '${(minutes / 60).round()} hours';
}

/// Builds an [ApiException] from a decoded body, tolerating the shapes DRF
/// emits when a view bypasses the project exception handler
/// (`{"detail": "..."}`, and a bare field map).
ApiException apiExceptionFrom(
  int status,
  Object? body, {
  Duration? retryAfter,
}) {
  if (body is Map<String, dynamic>) {
    final envelope = body['error'];
    if (envelope is Map<String, dynamic> && envelope.containsKey('detail')) {
      final extra = Map<String, dynamic>.from(envelope)
        ..remove('code')
        ..remove('status')
        ..remove('detail');
      final envelopeStatus = envelope['status'];
      return ApiException(
        status: envelopeStatus is int ? envelopeStatus : status,
        code: envelope['code'] is String
            ? envelope['code'] as String
            : ApiErrorCode.unknown,
        detail: envelope['detail'],
        extra: extra,
        retryAfter: retryAfter,
      );
    }
    final bare = body['detail'];
    if (bare is String) {
      return ApiException(
        status: status,
        code: ApiErrorCode.unknown,
        detail: bare,
        extra: body,
        retryAfter: retryAfter,
      );
    }
    return ApiException(
      status: status,
      code: ApiErrorCode.unknown,
      detail: body,
      extra: body,
      retryAfter: retryAfter,
    );
  }
  if (body is String && body.trim().isNotEmpty) {
    return ApiException(
      status: status,
      code: ApiErrorCode.unknown,
      detail: body.length > 400 ? body.substring(0, 400) : body,
      retryAfter: retryAfter,
    );
  }
  return ApiException(
    status: status,
    code: ApiErrorCode.unknown,
    detail: null,
    retryAfter: retryAfter,
  );
}

/// A user-facing sentence for any thrown value, so an error surface never
/// has to render a Dart exception.
String describeFailure(Object error) {
  if (error is ApiFailure) return error.message;
  return 'Something went wrong.';
}
