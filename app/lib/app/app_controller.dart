import 'dart:async';

import 'package:flutter/foundation.dart';

import '../core/routing/proxy_engine.dart';
import '../data/async_value.dart';
import '../data/mock/mock_data.dart';
import '../data/models/plan.dart';
import '../data/models/proxy.dart';
import '../data/models/proxy_chain.dart';
import '../data/models/subscription.dart';
import '../data/repositories/catalog_repository.dart';

/// Why an attempt to extend the chain was refused.
enum HopRejection {
  /// Plan's `max_chain_length` reached.
  chainLengthLimit,

  /// A premium proxy on a free-tier plan.
  premiumRequired,

  /// Already in the chain.
  duplicate,

  /// Cannot edit the chain while the tunnel is up.
  engineBusy,
}

/// Single app-wide store. Deliberately dependency-free: a `ChangeNotifier`
/// behind an `InheritedNotifier` is enough at this size, and it keeps the
/// pubspec to zero third-party packages while the engine question is open.
class AppController extends ChangeNotifier {
  AppController({required this.engine, required this.repository}) {
    _statusSub = engine.statusStream.listen((status) {
      _status = status;
      notifyListeners();
    });
    _statsSub = engine.statsStream.listen((stats) {
      _stats = stats;
      notifyListeners();
    });
  }

  final ProxyEngine engine;
  final CatalogRepository repository;

  StreamSubscription<EngineStatus>? _statusSub;
  StreamSubscription<ThroughputStats>? _statsSub;

  // --- Engine mirror -----------------------------------------------------
  EngineStatus _status = const EngineStatus.idle();
  EngineStatus get status => _status;

  ThroughputStats _stats = ThroughputStats.zero;
  ThroughputStats get stats => _stats;

  // --- Catalogue ---------------------------------------------------------
  AsyncValue<List<Proxy>> _proxies = const AsyncValue.loading();
  AsyncValue<List<Proxy>> get proxies => _proxies;

  AsyncValue<List<Plan>> _plans = const AsyncValue.loading();
  AsyncValue<List<Plan>> get plans => _plans;

  AsyncValue<Subscription> _subscription = const AsyncValue.loading();
  AsyncValue<Subscription> get subscription => _subscription;

  // --- Chain draft -------------------------------------------------------
  ProxyChain _chain = ProxyChain.empty(name: 'My route');
  ProxyChain get chain => _chain;

  // --- Browser filters ---------------------------------------------------
  String _query = '';
  String get query => _query;

  ProxyProtocol? _protocolFilter;
  ProxyProtocol? get protocolFilter => _protocolFilter;

  bool _aliveOnly = false;
  bool get aliveOnly => _aliveOnly;

  /// Max hops the UI should allow. Mirrors the server's answer; the server
  /// re-checks on every call (architecture.md section 3).
  int get maxChainLength =>
      _subscription.valueOrNull?.effectiveMaxChainLength ?? 1;

  bool get isFreeTier => _subscription.valueOrNull?.plan.isFreeTier ?? true;

  Future<void> load() async {
    _proxies = const AsyncValue.loading();
    _plans = const AsyncValue.loading();
    _subscription = const AsyncValue.loading();
    notifyListeners();

    await Future.wait<void>(<Future<void>>[
      _loadProxies(),
      _loadPlans(),
      _loadSubscription(),
    ]);

    // Seed a sensible starting chain the first time data arrives.
    final pool = _proxies.valueOrNull;
    if (_chain.isEmpty && pool != null && pool.isNotEmpty) {
      final seeded = MockData.defaultChain(pool);
      // The seeded preset is 3 hops; a free plan can only hold one. Trim it
      // and rename it, so the title never describes a route the user does
      // not actually have.
      _chain = seeded.hops.length <= maxChainLength
          ? seeded
          : seeded.copyWith(
              name: maxChainLength == 1 ? 'Single hop route' : 'Starter route',
              hops: seeded.hops.take(maxChainLength).toList(growable: false),
            );
      notifyListeners();
    }
  }

  Future<void> _loadProxies() async {
    try {
      final result = await repository.fetchProxies();
      _proxies = AsyncValue<List<Proxy>>.data(result);
    } on Object catch (e) {
      _proxies = AsyncValue<List<Proxy>>.error(
        'Could not load the proxy list.',
        detail: e.toString(),
      );
    }
    notifyListeners();
  }

  Future<void> _loadPlans() async {
    try {
      _plans = AsyncValue<List<Plan>>.data(await repository.fetchPlans());
    } on Object catch (e) {
      _plans = AsyncValue<List<Plan>>.error(
        'Could not load plans.',
        detail: e.toString(),
      );
    }
    notifyListeners();
  }

  Future<void> _loadSubscription() async {
    try {
      _subscription =
          AsyncValue<Subscription>.data(await repository.fetchSubscription());
    } on Object catch (e) {
      _subscription = AsyncValue<Subscription>.error(
        'Could not confirm your plan.',
        detail: e.toString(),
      );
    }
    notifyListeners();
  }

  Future<void> refreshProxies() => _loadProxies();

  // --- Filtering ---------------------------------------------------------
  void setQuery(String value) {
    if (_query == value) return;
    _query = value;
    notifyListeners();
  }

  void setProtocolFilter(ProxyProtocol? value) {
    if (_protocolFilter == value) return;
    _protocolFilter = value;
    notifyListeners();
  }

  void setAliveOnly(bool value) {
    if (_aliveOnly == value) return;
    _aliveOnly = value;
    notifyListeners();
  }

  void clearFilters() {
    if (_query.isEmpty && _protocolFilter == null && !_aliveOnly) return;
    _query = '';
    _protocolFilter = null;
    _aliveOnly = false;
    notifyListeners();
  }

  bool get hasActiveFilters =>
      _query.isNotEmpty || _protocolFilter != null || _aliveOnly;

  /// Filtered view of the pool. Computed on demand; the list is rendered with
  /// `ListView.builder`, so only the visible slice is ever laid out.
  List<Proxy> get visibleProxies {
    final pool = _proxies.valueOrNull;
    if (pool == null) return const <Proxy>[];
    final needle = _query.trim().toLowerCase();
    return pool.where((p) {
      if (_aliveOnly && !p.isAlive) return false;
      if (_protocolFilter != null && p.protocol != _protocolFilter) {
        return false;
      }
      if (needle.isEmpty) return true;
      return p.country.toLowerCase().contains(needle) ||
          p.city.toLowerCase().contains(needle) ||
          p.countryCode.toLowerCase().contains(needle) ||
          p.host.toLowerCase().contains(needle) ||
          p.protocol.badge.toLowerCase().contains(needle);
    }).toList(growable: false);
  }

  bool isInChain(Proxy proxy) =>
      _chain.hops.any((h) => h.proxy.id == proxy.id);

  // --- Chain editing -----------------------------------------------------
  /// Returns null on success, or the reason it was refused.
  HopRejection? addHop(Proxy proxy) {
    if (_status.isConnected || _status.isBusy) return HopRejection.engineBusy;
    if (isInChain(proxy)) return HopRejection.duplicate;
    if (proxy.isPremium && isFreeTier) return HopRejection.premiumRequired;
    if (_chain.length >= maxChainLength) return HopRejection.chainLengthLimit;

    _chain = _chain.withHopAdded(proxy);
    notifyListeners();
    return null;
  }

  void removeHopAt(int index) {
    if (_status.isConnected || _status.isBusy) return;
    _chain = _chain.withHopRemovedAt(index);
    notifyListeners();
  }

  void moveHop(int oldIndex, int newIndex) {
    if (_status.isConnected || _status.isBusy) return;
    _chain = _chain.withHopMoved(oldIndex, newIndex);
    notifyListeners();
  }

  void clearChain() {
    if (_status.isConnected || _status.isBusy) return;
    _chain = ProxyChain.empty(name: _chain.name);
    notifyListeners();
  }

  void renameChain(String name) {
    _chain = _chain.copyWith(name: name);
    notifyListeners();
  }

  // --- Engine ------------------------------------------------------------
  /// Connects, swallowing [EngineException] -- the failure is already on
  /// [status] and the UI renders it from there.
  Future<void> connect() async {
    try {
      await engine.connect(_chain);
    } on EngineException {
      // Surfaced through statusStream.
    }
  }

  Future<void> disconnect() => engine.disconnect();

  Future<void> toggleConnection() async {
    if (_status.isBusy) return;
    if (_status.isConnected) {
      await disconnect();
    } else {
      await connect();
    }
  }

  /// Stand-in for Google Play Billing. Real flow validates server-side
  /// (architecture.md section 4) -- nothing here is trusted.
  Future<void> simulatePurchase(Plan plan) async {
    final current = _subscription.valueOrNull;
    if (current == null) return;
    _subscription = const AsyncValue.loading();
    notifyListeners();
    await Future<void>.delayed(const Duration(milliseconds: 650));
    _subscription = AsyncValue<Subscription>.data(
      current.copyWith(
        plan: plan,
        status: SubscriptionStatus.active,
        expiresAt: DateTime.now().add(Duration(days: plan.durationDays)),
        bytesUsed: plan.isFreeTier ? current.bytesUsed : 0,
      ),
    );
    notifyListeners();
  }

  @override
  void dispose() {
    _statusSub?.cancel();
    _statsSub?.cancel();
    engine.dispose();
    super.dispose();
  }
}
