import 'dart:async';

import 'package:flutter/foundation.dart';

import '../data/api/api_client.dart';
import '../data/api/api_failure.dart';
import '../data/api/token_store.dart';
import '../data/models/auth_session.dart';

/// Where the app is in the sign-in lifecycle.
enum SessionState {
  /// Cold start: a refresh token may be in secure storage and we are finding
  /// out whether it still works. Renders a splash, never the sign-in form —
  /// flashing a login screen at a signed-in user and then replacing it is
  /// worse than a second of nothing.
  restoring,

  signedOut,

  /// A sign-in or sign-up request is in flight.
  authenticating,

  signedIn,
}

/// Owns the session: sign in, sign up, restore, sign out.
///
/// Deliberately separate from `AppController`. The catalogue controller
/// assumes a token exists; this one decides whether there is a session at
/// all, and the two have different lifetimes — signing out must tear down
/// catalogue state without destroying the controller that owns the engine.
class SessionController extends ChangeNotifier {
  SessionController({
    required this.client,
    required this.tokenStore,
    this.offlineMode = false,
  });

  final ApiClient client;
  final TokenStore tokenStore;

  /// True when the app was built against the mock repository. Sign-in is
  /// then a no-op that always succeeds — there is no server to ask.
  final bool offlineMode;

  SessionState _state = SessionState.restoring;
  SessionState get state => _state;

  AccountUser? _user;
  AccountUser? get user => _user;

  String? _signedInEmail;

  /// The address to show in the UI. Survives a cold restore, where the user
  /// object has not been fetched yet.
  String? get accountLabel => _user?.displayName ?? _signedInEmail;

  ApiFailure? _failure;

  /// The last sign-in failure, for the auth form. Cleared on the next
  /// attempt and on a successful sign-in.
  ApiFailure? get failure => _failure;

  /// `field -> message` from a 400, so the form can mark the right input.
  Map<String, String> get fieldErrors {
    final f = _failure;
    return f is ApiException ? f.fieldErrors : const <String, String>{};
  }

  bool get isSignedIn => _state == SessionState.signedIn;
  bool get isBusy => _state == SessionState.authenticating;

  /// Fires when a session ends, so `AppController` can drop catalogue state
  /// rather than leaving one account's proxy list on screen for the next.
  final List<VoidCallback> _signOutListeners = <VoidCallback>[];

  void addSignOutListener(VoidCallback listener) =>
      _signOutListeners.add(listener);

  void _notifySignedOut() {
    for (final listener in List<VoidCallback>.of(_signOutListeners)) {
      listener();
    }
  }

  void _set(SessionState next) {
    if (_state == next) return;
    _state = next;
    notifyListeners();
  }

  /// Cold start.
  ///
  /// There is never an access token at boot — it lives in memory only — so
  /// "do I have a session" is answered by whether the stored refresh token
  /// still mints one. Rather than call `/auth/refresh/` directly, this makes
  /// an ordinary authenticated request and lets [AuthRefreshTransport] do
  /// the refresh: one code path for both cases, so the boot path exercises
  /// the same rotation handling as every later 401.
  Future<void> restore() async {
    if (offlineMode) {
      _signedInEmail = 'offline@proxycrack.local';
      _set(SessionState.signedIn);
      return;
    }
    _set(SessionState.restoring);
    if (!await tokenStore.hasSession()) {
      _set(SessionState.signedOut);
      return;
    }
    _signedInEmail = await tokenStore.readUserEmail();
    try {
      await client.fetchAccount();
      _set(SessionState.signedIn);
    } on ApiFailure catch (error) {
      // Offline at launch is not a signed-out user. Dropping the session
      // because the train went into a tunnel would make the app demand a
      // password every morning on the commute.
      if (error.isOffline && _signedInEmail != null) {
        _set(SessionState.signedIn);
        return;
      }
      await tokenStore.clear();
      _signedInEmail = null;
      _set(SessionState.signedOut);
    }
  }

  Future<bool> signIn({
    required String email,
    required String password,
  }) async {
    return _authenticate(
      () => client.login(email: email, password: password),
    );
  }

  Future<bool> signUp({
    required String email,
    required String password,
    required String passwordConfirm,
  }) async {
    return _authenticate(
      () => client.register(
        email: email,
        password: password,
        passwordConfirm: passwordConfirm,
      ),
    );
  }

  Future<bool> _authenticate(Future<AuthSession> Function() request) async {
    _failure = null;
    _set(SessionState.authenticating);
    notifyListeners();
    try {
      final session = await request();
      await tokenStore.write(session.tokens);
      await tokenStore.writeUserEmail(session.user.email);
      _user = session.user;
      _signedInEmail = session.user.email;
      _failure = null;
      _set(SessionState.signedIn);
      notifyListeners();
      return true;
    } on ApiFailure catch (error) {
      _failure = error;
      _set(SessionState.signedOut);
      notifyListeners();
      return false;
    }
  }

  /// Local sign-out.
  ///
  /// There is no `/api/auth/logout/` on the mobile surface, so the refresh
  /// token is discarded rather than blacklisted. It stays valid until it
  /// expires; nothing on the device holds it any more. Worth knowing: a
  /// genuine remote revocation needs an endpoint that does not exist yet.
  Future<void> signOut() async {
    await tokenStore.clear();
    _user = null;
    _signedInEmail = null;
    _failure = null;
    _notifySignedOut();
    _set(SessionState.signedOut);
    notifyListeners();
  }

  /// Called by the transport when a refresh failed or a retried request came
  /// back 401 again. Idempotent — several in-flight requests can report the
  /// same lost session.
  Future<void> handleSessionLost() async {
    if (_state == SessionState.signedOut) return;
    await signOut();
  }

  void clearFailure() {
    if (_failure == null) return;
    _failure = null;
    notifyListeners();
  }
}
