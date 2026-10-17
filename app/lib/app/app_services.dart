import 'package:flutter/foundation.dart';

import '../core/routing/mock_proxy_engine.dart';
import '../core/routing/proxy_engine.dart';
import '../data/api/api_client.dart';
import '../data/api/api_config.dart';
import '../data/api/http_transport.dart';
import '../data/api/token_store.dart';
import '../data/api/transport.dart';
import '../data/repositories/catalog_repository.dart';
import 'session_controller.dart';

/// Composition root for everything that touches the network.
///
/// Built once in `main`, torn down once on dispose. This is the only place
/// that knows a concrete [Transport], [TokenStore] or [CatalogRepository]
/// exists — and, together with `ProxyCrackApp`, the only place that names a
/// concrete [ProxyEngine].
///
/// ## The wiring that matters
///
/// ```
/// HttpTransport          talks to Django, converts every wire failure
///   └─ AuthRefreshTransport   refresh-on-401, one retry, one in-flight
///        └─ ApiClient          typed endpoints
///             └─ ApiCatalogRepository   what the screens see
/// ```
///
/// The refresh hooks need the [SessionController] and the session controller
/// needs the client, so the circle is closed with a late binding rather than
/// by merging the two: the transport must not know what a screen is, and the
/// session controller must not know what a socket is.
class AppServices {
  AppServices._({
    required this.config,
    required this.tokenStore,
    required this.client,
    required this.repository,
    required this.session,
    required this.engine,
    required this.usesMockApi,
  });

  final ApiConfig config;
  final TokenStore tokenStore;
  final ApiClient client;
  final CatalogRepository repository;
  final SessionController session;
  final ProxyEngine engine;

  /// True when the app is running against [MockCatalogRepository].
  final bool usesMockApi;

  /// `--dart-define=PROXY_CRACK_USE_MOCK_API=true` keeps the mock data path
  /// selectable for offline UI work and for widget tests, which must not
  /// need a socket.
  static const bool _mockDefine = bool.fromEnvironment(
    'PROXY_CRACK_USE_MOCK_API',
  );

  factory AppServices.create({
    ApiConfig? config,
    bool? useMockApi,
    TokenStore? tokenStore,
    ProxyEngine? engine,
  }) {
    final resolvedConfig = config ?? ApiConfig.fromEnvironment();
    final mock = useMockApi ?? _mockDefine;
    // The mock path must not touch the platform keystore: a widget test has
    // no plugin registrar and every call would throw MissingPluginException.
    final store =
        tokenStore ?? (mock ? InMemoryTokenStore() : SecureTokenStore());

    late final SessionController session;

    final base = HttpTransport(
      config: resolvedConfig,
      readAccessToken: () => store.accessToken,
    );
    final transport = AuthRefreshTransport(
      base,
      AuthRefreshHooks(
        readRefreshToken: store.readRefreshToken,
        storeTokens: store.write,
        onSessionLost: () => session.handleSessionLost(),
      ),
    );
    final client = ApiClient(transport: transport, config: resolvedConfig);

    session = SessionController(
      client: client,
      tokenStore: store,
      offlineMode: mock,
    );

    if (kDebugMode && !mock) {
      debugPrint('Proxy Crack API base URL: ${resolvedConfig.baseUrl}');
      if (resolvedConfig.isCleartext) {
        debugPrint(
          'WARNING: talking to the API over cleartext HTTP. Fine against a '
          'local dev server, never acceptable against anything real.',
        );
      }
    }

    return AppServices._(
      config: resolvedConfig,
      tokenStore: store,
      client: client,
      repository: mock
          ? MockCatalogRepository()
          : ApiCatalogRepository(client),
      session: session,
      engine: engine ?? MockProxyEngine(),
      usesMockApi: mock,
    );
  }

  void dispose() {
    client.close();
    session.dispose();
  }
}
