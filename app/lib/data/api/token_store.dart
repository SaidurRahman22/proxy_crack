import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'transport.dart';

/// JWT custody.
///
/// Two different things with two different lifetimes:
///
/// * **Access token — memory only.** Thirty-minute lifetime, sent on every
///   request. Writing it to disk would multiply its exposure for no gain: a
///   cold start can always mint a new one from the refresh token, which is
///   exactly what happens.
/// * **Refresh token — secure storage.** Thirty-day lifetime, and whoever
///   holds it *is* the account until it expires. That makes it a credential,
///   and credentials do not go in `SharedPreferences`: on Android that is a
///   world-readable-to-root XML file in the app sandbox, trivially dumped
///   off a rooted or backed-up device. `flutter_secure_storage` puts it
///   behind the Android Keystore (and the iOS Keychain later).
///
/// Nothing here ever logs a token, including in `toString`.
abstract class TokenStore {
  String? get accessToken;

  /// True when a session is at least plausible — gates the boot probe.
  Future<bool> hasSession();

  Future<String?> readRefreshToken();

  /// Stores both halves. Called after login, register and every refresh.
  Future<void> write(TokenPair tokens);

  /// Replaces only the in-memory half.
  void setAccessToken(String? token);

  /// The signed-in address, so a cold start that restores a session can name
  /// the account before `/api/subscription/` answers.
  ///
  /// **Not a credential** — it grants nothing. It lives beside the refresh
  /// token only to avoid adding a second storage plugin for one string, and
  /// it is cleared by [clear] along with everything else.
  Future<String?> readUserEmail();

  Future<void> writeUserEmail(String? email);

  /// Wipes everything. Sign-out and unrecoverable 401.
  Future<void> clear();
}

/// Production implementation.
class SecureTokenStore implements TokenStore {
  SecureTokenStore({FlutterSecureStorage? storage})
    : _storage =
          storage ??
          const FlutterSecureStorage(
            // v11 is Keystore-backed (AES-GCM, RSA-OAEP key wrapping) with
            // no plaintext fallback, which is the whole reason this is not
            // SharedPreferences. `resetOnError` defaults to true: a keystore
            // an OS upgrade or a restored backup left unreadable wipes the
            // token and the user signs in again, rather than the app
            // crashing on every launch.
            aOptions: AndroidOptions(preferencesKeyPrefix: 'proxycrack'),
          );

  static const String refreshKey = 'pc.mobile.refresh';
  static const String emailKey = 'pc.mobile.email';

  final FlutterSecureStorage _storage;

  String? _access;

  /// Mirrors the stored token so `hasSession` does not hit the Keystore on
  /// every build. Null means "not read yet", not "absent".
  String? _cachedRefresh;
  bool _refreshLoaded = false;

  @override
  String? get accessToken => _access;

  @override
  void setAccessToken(String? token) => _access = token;

  @override
  Future<String?> readRefreshToken() async {
    if (_refreshLoaded) return _cachedRefresh;
    try {
      _cachedRefresh = await _storage.read(key: refreshKey);
    } on Object catch (error) {
      // A corrupted keystore entry (OS upgrade, restored backup) throws.
      // Treat it as "no session" rather than bricking the app on launch.
      debugPrint('Secure storage read failed; signing out. $error');
      _cachedRefresh = null;
    }
    _refreshLoaded = true;
    return _cachedRefresh;
  }

  @override
  Future<bool> hasSession() async {
    if (_access != null) return true;
    final refresh = await readRefreshToken();
    return refresh != null && refresh.isNotEmpty;
  }

  @override
  Future<void> write(TokenPair tokens) async {
    _access = tokens.access;
    // An empty `refresh` means the server did not rotate; keep what we have
    // rather than erasing a working token.
    if (tokens.refresh.isEmpty) return;
    _cachedRefresh = tokens.refresh;
    _refreshLoaded = true;
    try {
      await _storage.write(key: refreshKey, value: tokens.refresh);
    } on Object catch (error) {
      // The session still works for this process; it just will not survive
      // a restart. Degrading is better than refusing to sign in.
      debugPrint('Secure storage write failed; session is memory-only. $error');
    }
  }

  @override
  Future<String?> readUserEmail() async {
    try {
      return await _storage.read(key: emailKey);
    } on Object {
      return null;
    }
  }

  @override
  Future<void> writeUserEmail(String? email) async {
    try {
      if (email == null || email.isEmpty) {
        await _storage.delete(key: emailKey);
      } else {
        await _storage.write(key: emailKey, value: email);
      }
    } on Object {
      /* Cosmetic only — the session still works without it. */
    }
  }

  @override
  Future<void> clear() async {
    _access = null;
    _cachedRefresh = null;
    _refreshLoaded = true;
    try {
      await _storage.delete(key: refreshKey);
      await _storage.delete(key: emailKey);
    } on Object catch (error) {
      debugPrint('Secure storage delete failed. $error');
    }
  }
}

/// Memory-only store for tests and for the mock repository, where touching
/// the platform keystore would require a running engine.
class InMemoryTokenStore implements TokenStore {
  String? _access;
  String? _refresh;
  String? _email;

  @override
  Future<String?> readUserEmail() async => _email;

  @override
  Future<void> writeUserEmail(String? email) async => _email = email;

  @override
  String? get accessToken => _access;

  @override
  void setAccessToken(String? token) => _access = token;

  @override
  Future<String?> readRefreshToken() async => _refresh;

  @override
  Future<bool> hasSession() async =>
      _access != null || (_refresh != null && _refresh!.isNotEmpty);

  @override
  Future<void> write(TokenPair tokens) async {
    _access = tokens.access;
    if (tokens.refresh.isNotEmpty) _refresh = tokens.refresh;
  }

  @override
  Future<void> clear() async {
    _access = null;
    _refresh = null;
    _email = null;
  }
}
