import 'package:flutter_test/flutter_test.dart';
import 'package:proxy_crack/app/app_controller.dart';
import 'package:proxy_crack/core/routing/mock_proxy_engine.dart';
import 'package:proxy_crack/data/api/api_failure.dart';
import 'package:proxy_crack/data/mock/mock_data.dart';
import 'package:proxy_crack/data/models/account_snapshot.dart';
import 'package:proxy_crack/data/models/announcement.dart';
import 'package:proxy_crack/data/models/chain_diversity.dart';
import 'package:proxy_crack/data/models/entitlements.dart';
import 'package:proxy_crack/data/models/proxy.dart';
import 'package:proxy_crack/data/models/proxy_credentials.dart';
import 'package:proxy_crack/data/models/resolved_chain.dart';
import 'package:proxy_crack/data/models/saved_chain.dart';
import 'package:proxy_crack/data/repositories/catalog_repository.dart';

/// Counts every call, so the tests can assert on *when* the expensive,
/// throttled, audit-logged endpoints are hit — which is the whole point of
/// D18's credential design.
class RecordingRepository implements CatalogRepository {
  RecordingRepository({
    this.resolveFailure,
    this.saveFailure,
    this.maxChainLength = 3,
    this.allowedTiers = const <String>['free', 'premium'],
  });

  final ApiFailure? resolveFailure;
  final ApiFailure? saveFailure;

  /// Entitlements the fake server reports. The real free plan allows one
  /// hop, which would make every multi-hop assertion here untestable.
  final int maxChainLength;
  final List<String> allowedTiers;

  final List<String> calls = <String>[];
  int credentialFetches = 0;
  int perHopCredentialCalls = 0;

  List<Proxy> pool = MockData.proxies(count: 30);

  int count(String name) => calls.where((c) => c == name).length;

  @override
  Future<List<Proxy>> fetchProxies() async {
    calls.add('proxies');
    return pool;
  }

  @override
  Future<AccountSnapshot> fetchAccount() async {
    calls.add('account');
    final base = MockData.account;
    return AccountSnapshot(
      plan: base.plan,
      subscription: base.subscription,
      entitlements: Entitlements(
        maxChainLength: maxChainLength,
        maxDevices: 3,
        allowedProxyTiers: allowedTiers,
        maxProxyTier: allowedTiers.last,
        bandwidthLimitBytes: 0,
        bandwidthUsedBytes: 0,
        bandwidthRemainingBytes: null,
        isFreeTier: false,
        source: 'subscription',
      ),
      availablePlans: base.availablePlans,
      hasSubscriptionRow: true,
    );
  }

  @override
  Future<List<Announcement>> fetchAnnouncements() async {
    calls.add('announcements');
    return MockData.announcements;
  }

  @override
  Future<List<SavedChain>> fetchChains() async {
    calls.add('chains');
    return const <SavedChain>[];
  }

  @override
  Future<SavedChain> saveChain({
    int? id,
    required String name,
    required List<int> proxyIds,
  }) async {
    calls.add('saveChain');
    final failure = saveFailure;
    if (failure != null) throw failure;
    return MockData.savedChain(
      id: id ?? 500,
      name: name,
      proxyIds: proxyIds,
      pool: pool,
    );
  }

  @override
  Future<void> deleteChain(int id) async => calls.add('deleteChain');

  @override
  Future<ResolvedChain> resolveChain(int chainId) async {
    calls.add('resolve');
    final failure = resolveFailure;
    if (failure != null) throw failure;
    return MockData.resolve(
      MockData.savedChain(
        id: chainId,
        name: 'x',
        proxyIds: _lastSavedIds,
        pool: pool,
      ),
    );
  }

  /// The connect path. Recorded separately from `resolve` so a test can
  /// assert that connecting does NOT save a chain — the regression that
  /// made a second connect fail with "you already have a chain with this
  /// name".
  @override
  Future<ResolvedChain> resolveProxies(List<int> proxyIds) async {
    calls.add('resolve_adhoc');
    final failure = resolveFailure;
    if (failure != null) throw failure;
    return MockData.resolve(
      MockData.savedChain(id: 0, name: '', proxyIds: proxyIds, pool: pool),
    );
  }

  List<int> _lastSavedIds = const <int>[];
  set lastSavedIds(List<int> value) => _lastSavedIds = value;

  @override
  Future<ChainDiversityReport> previewDiversity(List<int> proxyIds) async {
    calls.add('diversity');
    return MockData.diversity(proxyIds, pool);
  }

  @override
  Future<ChainDiversityReport> fetchChainDiversity(int chainId) async {
    calls.add('chainDiversity');
    return MockData.diversity(const <int>[], pool);
  }

  @override
  Future<ChainCredentials> fetchChainCredentials(ResolvedChain resolved) async {
    calls.add('credentials');
    credentialFetches++;
    perHopCredentialCalls += resolved.hops.length;
    return ChainCredentials(<int, ProxyCredentials>{
      for (final hop in resolved.hops)
        hop.proxy.id: ProxyCredentials(
          proxyId: hop.proxy.id,
          username: 'u',
          password: 'p',
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
    calls.add('usage');
  }
}

void main() {
  late RecordingRepository repository;
  late AppController controller;

  AppController build({RecordingRepository? repo}) {
    repository = repo ?? RecordingRepository();
    return controller = AppController(
      engine: MockProxyEngine(speedMultiplier: 1000),
      repository: repository,
    );
  }

  tearDown(() => controller.dispose());

  test('loading the app fetches NO credentials', () async {
    build();
    await controller.load();

    expect(repository.count('account'), 1);
    expect(repository.count('proxies'), 1);
    expect(repository.count('announcements'), 1);
    // D18's whole point: the credential endpoint is 60/hour and audit
    // logged. A screen that touched it on load would burn the budget doing
    // nothing and write an audit row per render.
    expect(repository.count('credentials'), 0);
    expect(repository.count('resolve'), 0);
  });

  test('editing the chain analyses it but still fetches no credentials',
      () async {
    build();
    await controller.load();
    final healthy = repository.pool.where((p) => p.isAlive).toList();

    controller.addHop(healthy.first);
    await controller.refreshDiversity();

    expect(repository.count('diversity'), greaterThanOrEqualTo(1));
    expect(repository.count('credentials'), 0);
  });

  test('connect resolves WITHOUT saving, fetches credentials once per hop, '
      'then starts the engine', () async {
    build();
    await controller.load();
    final healthy = repository.pool.where((p) => p.isAlive).toList();
    controller.addHop(healthy.first);

    await controller.connect();

    // Connecting must not persist a preset: it spends the write budget and
    // leaves a saved chain the user never asked for.
    expect(repository.count('saveChain'), 0);
    expect(repository.count('resolve_adhoc'), 1);
    expect(repository.credentialFetches, 1);
    expect(repository.perHopCredentialCalls, 1);
    expect(controller.connectStage, ConnectStage.idle);
    expect(controller.connectFailure, isNull);
  });

  test('a 3-hop connect costs exactly 3 credential calls', () async {
    build();
    await controller.load();
    final healthy = repository.pool.where((p) => p.isAlive).take(3).toList();
    for (final proxy in healthy) {
      expect(controller.addHop(proxy), isNull, reason: 'hop was refused');
    }
    final ids = controller.chain.hops
        .map((h) => h.proxy.id)
        .toList(growable: false);
    expect(ids, hasLength(3));
    repository.lastSavedIds = ids;

    await controller.connect();
    // Three hops, three credential calls, three audit rows, three of the
    // 60/hour budget. That is the price D18 chose over one bulk disclosure.
    expect(repository.perHopCredentialCalls, 3);
  });

  test('a failed resolve never reaches the credential endpoint', () async {
    build(
      repo: RecordingRepository(
        resolveFailure: ApiException(
          status: 409,
          code: ApiErrorCode.chainUnresolvable,
          detail: <String, dynamic>{
            'message': 'No healthy proxy satisfies hop 1.',
            'code': ChainResolutionCode.hopProxyDead,
            'position': 0,
          },
        ),
      ),
    );
    await controller.load();
    controller.addHop(repository.pool.firstWhere((p) => p.isAlive));

    await controller.connect();

    expect(repository.count('resolve_adhoc'), 1);
    expect(repository.count('credentials'), 0);
    final failure = controller.connectFailure;
    expect(failure, isA<ApiException>());
    expect(
      (failure! as ApiException).resolutionCode,
      ChainResolutionCode.hopProxyDead,
    );
    expect(controller.connectStage, ConnectStage.idle);
  });

  test('an entitlement refusal on resolve stops the sequence immediately',
      () async {
    // Entitlement is now enforced on the resolve path, because that is the
    // only call connect makes. The server re-checks it there regardless of
    // what the client believes.
    build(
      repo: RecordingRepository(
        resolveFailure: ApiException(
          status: 403,
          code: ApiErrorCode.chainLengthExceeded,
          detail: <String, dynamic>{
            'message': 'Chain is longer than your plan allows.',
            'max_chain_length': 1,
            'requested_length': 3,
          },
        ),
      ),
    );
    await controller.load();
    controller.addHop(repository.pool.firstWhere((p) => p.isAlive));

    await controller.connect();

    expect(repository.count('saveChain'), 0);
    expect(repository.count('resolve_adhoc'), 1);
    expect(repository.count('credentials'), 0);
    expect(
      (controller.connectFailure! as ApiException).detailValue(
        'max_chain_length',
      ),
      1,
    );
  });

  test('connecting twice in a row works — connect never saves a chain',
      () async {
    // Regression. Connect used to `POST /api/chains/` with a hardcoded
    // name, so the SECOND connect hit the per-user unique-name constraint
    // and failed with "You already have a chain with this name" — the app
    // could not connect a second time at all. It also left a saved preset
    // the user never asked for, and resolving the server's copy meant
    // connecting through hops that might no longer match the screen.
    build();
    await controller.load();
    final healthy = repository.pool.where((p) => p.isAlive).take(2).toList();

    controller.addHop(healthy[0]);
    await controller.connect();
    expect(controller.connectFailure, isNull, reason: 'first connect');

    await controller.disconnect();
    // The controller mirrors the engine through a stream, so idle lands one
    // event-loop turn after `disconnect` returns.
    await Future<void>.delayed(const Duration(milliseconds: 10));

    await controller.connect();
    expect(controller.connectFailure, isNull, reason: 'second connect');

    expect(repository.count('saveChain'), 0, reason: 'nothing was persisted');
    expect(repository.count('resolve_adhoc'), 2);
  });

  test('sign-out drops every trace of the previous account', () async {
    build();
    await controller.load();
    controller.addHop(repository.pool.firstWhere((p) => p.isAlive));
    expect(controller.proxies.valueOrNull, isNotNull);

    controller.resetForSignOut();

    expect(controller.proxies.valueOrNull, isNull);
    expect(controller.account.valueOrNull, isNull);
    expect(controller.chain.isEmpty, isTrue);
    expect(controller.savedChainId, isNull);
    expect(controller.diversity, isNull);
    expect(controller.announcements, isEmpty);
  });
}
