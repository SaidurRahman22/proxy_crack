import 'dart:async';

import 'package:proxy_crack/data/api/api_failure.dart';
import 'package:proxy_crack/data/api/transport.dart';

/// A scripted [Transport] for testing the layers above the socket.
///
/// Records every request in order, so a test can assert *how many* calls
/// were made — which is the whole point when the thing under test is a
/// refresh that must not stampede, or a credential fetch that must happen
/// once per hop and not once per rebuild.
class FakeTransport implements Transport {
  FakeTransport(this.handler);

  /// Receives the spec and the 0-based call index for that path.
  final FutureOr<Object?> Function(RequestSpec spec, int callIndex) handler;

  final List<RequestSpec> requests = <RequestSpec>[];

  int countFor(String path) =>
      requests.where((r) => r.path == path).length;

  @override
  Future<Object?> send(RequestSpec spec) async {
    final index = countFor(spec.path);
    requests.add(spec);
    return handler(spec, index);
  }

  @override
  void close() {}
}

/// Convenience: the `{"error": {...}}` envelope the backend always emits.
Map<String, dynamic> errorEnvelope({
  required String code,
  required int status,
  required Object detail,
  Map<String, dynamic> extra = const <String, dynamic>{},
}) => <String, dynamic>{
  'error': <String, dynamic>{
    'code': code,
    'status': status,
    'detail': detail,
    ...extra,
  },
};

/// Throws the way `HttpTransport` would for a given status and body.
Never throwApi(int status, Object? body, {Duration? retryAfter}) {
  throw apiExceptionFrom(status, body, retryAfter: retryAfter);
}
