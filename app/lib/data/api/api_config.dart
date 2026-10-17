import 'package:flutter/foundation.dart';

/// Where the app talks to Django.
///
/// ## The emulator trap
///
/// Inside the Android emulator `127.0.0.1` is the *emulator's own* loopback,
/// not the development machine. A debug build pointed at `127.0.0.1:8000`
/// fails with a connection refused that looks exactly like "the backend is
/// down", and costs an hour. The emulator reaches the host through the
/// special address `10.0.2.2`, which is why that is the Android default
/// here.
///
/// ## Overriding it
///
/// ```
/// flutter run --dart-define=PROXY_CRACK_API_BASE_URL=http://192.168.1.20:8000
/// flutter build apk --dart-define=PROXY_CRACK_API_BASE_URL=https://api.example.com
/// ```
///
/// A physical device on the same Wi-Fi needs the machine's LAN address and
/// the backend started with `runserver 0.0.0.0:8000`. See `app/README-api.md`.
///
/// ⚠️ Django's `ALLOWED_HOSTS` defaults to `localhost,127.0.0.1,[::1]`, so a
/// request arriving with `Host: 10.0.2.2:8000` is rejected with a 400 before
/// it reaches a view. The backend must be started with `10.0.2.2` in
/// `DJANGO_ALLOWED_HOSTS`. This is an environment setting, not a code change.
@immutable
class ApiConfig {
  const ApiConfig({required this.baseUrl, this.timeout = defaultTimeout});

  /// Origin only, no trailing slash: `http://10.0.2.2:8000`.
  final String baseUrl;

  /// Per-request ceiling. Deliberately generous — the first request after a
  /// cold Django start can take several seconds — but finite, because a
  /// request with no timeout on a mobile network hangs a screen forever.
  final Duration timeout;

  static const Duration defaultTimeout = Duration(seconds: 20);

  /// Compile-time override. Empty when the define was not passed.
  static const String _define = String.fromEnvironment(
    'PROXY_CRACK_API_BASE_URL',
  );

  /// `10.0.2.2` on Android, loopback everywhere else (desktop targets — D8).
  static String get _platformDefault {
    if (kIsWeb) return 'http://127.0.0.1:8000';
    return defaultTargetPlatform == TargetPlatform.android
        ? 'http://10.0.2.2:8000'
        : 'http://127.0.0.1:8000';
  }

  /// The configuration the app actually boots with.
  factory ApiConfig.fromEnvironment() {
    final raw = _define.trim().isEmpty ? _platformDefault : _define.trim();
    return ApiConfig(baseUrl: normalise(raw));
  }

  /// Strips a trailing slash so `'$baseUrl$path'` never produces `//api/`.
  static String normalise(String value) {
    var out = value.trim();
    while (out.endsWith('/')) {
      out = out.substring(0, out.length - 1);
    }
    return out;
  }

  /// True when traffic leaves the device unencrypted.
  ///
  /// Fine against a local dev server; never acceptable against anything
  /// real. The app surfaces it rather than hiding it.
  bool get isCleartext => baseUrl.startsWith('http://');

  Uri resolve(String path) => Uri.parse('$baseUrl$path');

  @override
  String toString() => 'ApiConfig($baseUrl)';
}
