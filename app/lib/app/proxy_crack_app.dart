import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/routing/proxy_engine.dart';
import '../core/theme/app_dimens.dart';
import '../core/theme/app_theme.dart';
import '../data/repositories/catalog_repository.dart';
import '../features/auth/auth_screen.dart';
import '../features/shell/app_shell.dart';
import 'app_controller.dart';
import 'app_scope.dart';
import 'app_services.dart';
import 'session_controller.dart';

/// Composition root.
///
/// This is the only place that names a concrete [ProxyEngine] and the only
/// place that decides whether the app talks to Django or to the mock.
/// Dropping in the real Xray engine later is a one-line change here — see
/// architecture.md §2.
class ProxyCrackApp extends StatefulWidget {
  const ProxyCrackApp({
    super.key,
    this.services,
    this.engineOverride,
    this.repositoryOverride,
  });

  /// Pre-built services. When null, built from `--dart-define`s.
  final AppServices? services;

  /// Injection points for tests. Passing [repositoryOverride] also puts the
  /// session into offline mode, so a widget test needs no socket and no
  /// platform keystore.
  final ProxyEngine? engineOverride;
  final CatalogRepository? repositoryOverride;

  @override
  State<ProxyCrackApp> createState() => _ProxyCrackAppState();
}

class _ProxyCrackAppState extends State<ProxyCrackApp> {
  late final AppServices _services;
  late final AppController _controller;
  late final bool _ownsServices;

  @override
  void initState() {
    super.initState();
    _ownsServices = widget.services == null;
    _services =
        widget.services ??
        AppServices.create(
          // A supplied repository means a test or an offline run: never
          // reach for the network or the platform keystore.
          useMockApi: widget.repositoryOverride != null ? true : null,
          engine: widget.engineOverride,
        );

    _controller = AppController(
      engine: widget.engineOverride ?? _services.engine,
      repository: widget.repositoryOverride ?? _services.repository,
    );

    // Signing out must not leave the previous account's proxy list on
    // screen for the next one.
    _services.session.addSignOutListener(_controller.resetForSignOut);
    _services.session.addListener(_onSession);

    // The listener is what triggers the catalogue load, so this is fire and
    // forget. Loading here as well would double every request on the mock
    // path, where `restore` completes synchronously.
    _services.session.restore();
  }

  bool _loaded = false;

  void _onSession() {
    final session = _services.session;
    if (session.isSignedIn && !_loaded) {
      _loaded = true;
      _controller.load();
    } else if (!session.isSignedIn) {
      _loaded = false;
    }
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    _services.session.removeListener(_onSession);
    _controller.dispose();
    if (_ownsServices) _services.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: AppTheme.systemOverlay,
      child: AppScope(
        controller: _controller,
        child: MaterialApp(
          title: 'Proxy Crack',
          debugShowCheckedModeBanner: false,
          themeMode: ThemeMode.dark,
          theme: AppTheme.dark,
          darkTheme: AppTheme.dark,
          home: _Gate(
            services: _services,
            onSignOut: () => _services.session.signOut(),
          ),
        ),
      ),
    );
  }
}

/// Chooses between the splash, the sign-in form and the shell.
///
/// A cold start with a stored refresh token shows the splash while the
/// token is exchanged, never the sign-in form — flashing a login screen at
/// a signed-in user and then replacing it reads as a bug.
class _Gate extends StatelessWidget {
  const _Gate({required this.services, required this.onSignOut});

  final AppServices services;
  final VoidCallback onSignOut;

  @override
  Widget build(BuildContext context) {
    final session = services.session;
    return switch (session.state) {
      SessionState.restoring => const _Splash(),
      SessionState.signedIn => AppShell(
        accountLabel: session.accountLabel,
        onSignOut: onSignOut,
      ),
      SessionState.signedOut ||
      SessionState.authenticating => AuthScreen(
        session: session,
        config: services.config,
      ),
    };
  }
}

class _Splash extends StatelessWidget {
  const _Splash();

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Container(
              width: 56,
              height: 56,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: scheme.primary,
                borderRadius: BorderRadius.circular(AppRadius.card),
              ),
              child: Icon(
                Icons.hub_outlined,
                size: 28,
                color: scheme.onPrimary,
              ),
            ),
            const SizedBox(height: AppSpacing.lg),
            Semantics(
              liveRegion: true,
              child: Text(
                'Restoring your session',
                style: context.text.bodyMedium,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
