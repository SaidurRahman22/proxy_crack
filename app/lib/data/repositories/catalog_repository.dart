import 'dart:async';

import '../api/api_client.dart';
import '../api/api_failure.dart';
import '../mock/mock_data.dart';
import '../models/account_snapshot.dart';
import '../models/announcement.dart';
import '../models/chain_diversity.dart';
import '../models/proxy.dart';
import '../models/proxy_credentials.dart';
import '../models/resolved_chain.dart';
import '../models/saved_chain.dart';

/// Read and write side of the app's data.
///
/// Two implementations, chosen at composition time:
/// [ApiCatalogRepository] against Django, and [MockCatalogRepository] in
/// memory for widget tests and offline UI work. **No screen knows which one
/// it got.**
///
/// ## Why this interface changed shape
///
/// The original had `fetchProxies` / `fetchPlans` / `fetchSubscription`.
/// `GET /api/subscription/` returns the plan, the subscription, the resolved
/// entitlements *and* the plan catalogue in one response, so asking for
/// plans and subscription separately meant two round trips for one server
/// answer, and gave the client two copies of the limits that could disagree.
/// That was the abstraction being wrong, not the screens — so the interface
/// moved to [fetchAccount] and `AppController` derives both from it. The
/// screens still read `controller.plans` and `controller.subscription`.
abstract class CatalogRepository {
  /// `GET /api/proxies/` — already entitlement-scoped by the server, and
  /// ordered best-first by `quality_score` rather than fastest-first.
  Future<List<Proxy>> fetchProxies();

  /// `GET /api/subscription/` — plan, subscription, entitlements, catalogue.
  Future<AccountSnapshot> fetchAccount();

  /// `GET /api/content/announcements/`.
  Future<List<Announcement>> fetchAnnouncements();

  /// `GET /api/chains/` — the user's saved presets.
  Future<List<SavedChain>> fetchChains();

  /// Creates or replaces a saved chain. [id] null means create.
  Future<SavedChain> saveChain({
    int? id,
    required String name,
    required List<int> proxyIds,
  });

  Future<void> deleteChain(int id);

  /// `POST /api/chains/{id}/resolve/`. Point-in-time; never cache the result.
  Future<ResolvedChain> resolveChain(int chainId);

  /// `POST /api/chains/resolve/` — resolve an ad-hoc chain, saving nothing.
  /// This is the connect path: connecting must not create a saved preset.
  Future<ResolvedChain> resolveProxies(List<int> proxyIds);

  /// `POST /api/chains/diversity/` — score an unsaved chain.
  Future<ChainDiversityReport> previewDiversity(List<int> proxyIds);

  /// `GET /api/chains/{id}/diversity/` — score a saved chain, resolved.
  Future<ChainDiversityReport> fetchChainDiversity(int chainId);

  /// Fetches one set of credentials per hop, at **connect time only**.
  ///
  /// D18: `/resolve/` returns identities and a `credentials_url` per hop,
  /// never credentials inline, so a three-hop connect costs three calls
  /// under the 60/hour `proxy_credentials` throttle, each audit-logged.
  /// Implementations must not cache, persist or log the result.
  Future<ChainCredentials> fetchChainCredentials(ResolvedChain resolved);

  /// `POST /api/usage/` once a session ends.
  Future<void> reportUsage({
    required int bytesUp,
    required int bytesDown,
    required DateTime sessionStart,
    required DateTime sessionEnd,
  });
}

/// The real thing.
class ApiCatalogRepository implements CatalogRepository {
  ApiCatalogRepository(this.client);

  final ApiClient client;

  @override
  Future<List<Proxy>> fetchProxies() => client.fetchAllProxies();

  @override
  Future<AccountSnapshot> fetchAccount() => client.fetchAccount();

  @override
  Future<List<Announcement>> fetchAnnouncements() =>
      client.fetchAnnouncements();

  @override
  Future<List<SavedChain>> fetchChains() => client.fetchChains();

  @override
  Future<SavedChain> saveChain({
    int? id,
    required String name,
    required List<int> proxyIds,
  }) async {
    if (id == null) {
      return client.createChain(name: name, proxyIds: proxyIds);
    }
    try {
      return await client.updateChain(id: id, name: name, proxyIds: proxyIds);
    } on ApiException catch (error) {
      // The chain was deleted on another device, or belongs to a session
      // that has since changed. Recreating is the right recovery; failing
      // the connect because a preset vanished is not.
      if (error.status == 404) {
        return client.createChain(name: name, proxyIds: proxyIds);
      }
      rethrow;
    }
  }

  @override
  Future<void> deleteChain(int id) => client.deleteChain(id);

  @override
  Future<ResolvedChain> resolveChain(int chainId) =>
      client.resolveChain(chainId);

  @override
  Future<ResolvedChain> resolveProxies(List<int> proxyIds) =>
      client.resolveProxies(proxyIds);

  @override
  Future<ChainDiversityReport> previewDiversity(List<int> proxyIds) =>
      client.previewDiversity(proxyIds);

  @override
  Future<ChainDiversityReport> fetchChainDiversity(int chainId) =>
      client.fetchChainDiversity(chainId);

  @override
  Future<ChainCredentials> fetchChainCredentials(ResolvedChain resolved) async {
    final byProxy = <int, ProxyCredentials>{};
    // Sequential, not `Future.wait`. Three parallel requests against a
    // 60/hour throttle is not faster in any way that matters, and a burst
    // makes the 429 arrive for two hops at once instead of one — which is
    // harder to report honestly than "hop 2 was rate limited".
    for (final hop in resolved.hops) {
      final credentials = await client.fetchCredentials(
        proxyId: hop.proxy.id,
        url: hop.credentialsUrl,
      );
      byProxy[hop.proxy.id] = credentials;
    }
    return ChainCredentials(byProxy);
  }

  @override
  Future<void> reportUsage({
    required int bytesUp,
    required int bytesDown,
    required DateTime sessionStart,
    required DateTime sessionEnd,
  }) => client.reportUsage(
    bytesUp: bytesUp,
    bytesDown: bytesDown,
    sessionStart: sessionStart,
    sessionEnd: sessionEnd,
  );
}

/// In-memory implementation with realistic delays, so loading states get
/// exercised during development rather than only in production.
///
/// Kept deliberately: widget tests must not need a socket, and UI work on a
/// plane must not need Django. Select it with
/// `--dart-define=PROXY_CRACK_USE_MOCK_API=true`.
class MockCatalogRepository implements CatalogRepository {
  MockCatalogRepository({this.latency = const Duration(milliseconds: 900)});

  final Duration latency;

  List<Proxy>? _cachedProxies;
  final List<SavedChain> _chains = <SavedChain>[];
  int _nextChainId = 9100;

  @override
  Future<List<Proxy>> fetchProxies() async {
    await Future<void>.delayed(latency);
    return _cachedProxies ??= MockData.proxies();
  }

  @override
  Future<AccountSnapshot> fetchAccount() async {
    await Future<void>.delayed(latency * 0.5);
    return MockData.account;
  }

  @override
  Future<List<Announcement>> fetchAnnouncements() async {
    await Future<void>.delayed(latency * 0.4);
    return MockData.announcements;
  }

  @override
  Future<List<SavedChain>> fetchChains() async {
    await Future<void>.delayed(latency * 0.4);
    return List<SavedChain>.unmodifiable(_chains);
  }

  @override
  Future<SavedChain> saveChain({
    int? id,
    required String name,
    required List<int> proxyIds,
  }) async {
    await Future<void>.delayed(latency * 0.4);
    final pool = await fetchProxies();
    final saved = MockData.savedChain(
      id: id ?? _nextChainId++,
      name: name,
      proxyIds: proxyIds,
      pool: pool,
    );
    _chains
      ..removeWhere((c) => c.id == saved.id)
      ..add(saved);
    return saved;
  }

  @override
  Future<void> deleteChain(int id) async {
    await Future<void>.delayed(latency * 0.2);
    _chains.removeWhere((c) => c.id == id);
  }

  @override
  Future<ResolvedChain> resolveChain(int chainId) async {
    await Future<void>.delayed(latency * 0.5);
    final chain = _chains.firstWhere(
      (c) => c.id == chainId,
      orElse: () => throw ApiException(
        status: 404,
        code: ApiErrorCode.notFound,
        detail: 'No chain with id $chainId.',
      ),
    );
    return MockData.resolve(chain);
  }

  @override
  Future<ResolvedChain> resolveProxies(List<int> proxyIds) async {
    await Future<void>.delayed(latency * 0.5);
    if (proxyIds.isEmpty) {
      throw ApiException(
        status: 409,
        code: ApiErrorCode.chainUnresolvable,
        detail: 'A chain needs at least one hop.',
      );
    }
    // Resolves an ad-hoc list without touching `_chains`, mirroring the
    // live path: connecting saves nothing.
    return MockData.resolveProxyIds(proxyIds);
  }

  @override
  Future<ChainDiversityReport> previewDiversity(List<int> proxyIds) async {
    await Future<void>.delayed(latency * 0.4);
    final pool = await fetchProxies();
    return MockData.diversity(proxyIds, pool);
  }

  @override
  Future<ChainDiversityReport> fetchChainDiversity(int chainId) async {
    final chain = _chains.firstWhere(
      (c) => c.id == chainId,
      orElse: () => throw ApiException(
        status: 404,
        code: ApiErrorCode.notFound,
        detail: 'No chain with id $chainId.',
      ),
    );
    return previewDiversity(
      chain.hops
          .map((h) => h.proxy?.id)
          .whereType<int>()
          .toList(growable: false),
    );
  }

  @override
  Future<ChainCredentials> fetchChainCredentials(ResolvedChain resolved) async {
    await Future<void>.delayed(latency * 0.3);
    return ChainCredentials(<int, ProxyCredentials>{
      for (final hop in resolved.hops)
        hop.proxy.id: ProxyCredentials(
          proxyId: hop.proxy.id,
          username: 'mock-user-${hop.proxy.id}',
          password: 'mock-not-a-real-credential',
          ssMethod: hop.proxy.protocol == ProxyProtocol.shadowsocks
              ? 'aes-256-gcm'
              : '',
        ),
    });
  }

  @override
  Future<void> reportUsage({
    required int bytesUp,
    required int bytesDown,
    required DateTime sessionStart,
    required DateTime sessionEnd,
  }) async {
    await Future<void>.delayed(latency * 0.2);
  }
}
