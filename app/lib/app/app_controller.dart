import 'dart:async';

import 'package:flutter/foundation.dart';

import '../core/routing/proxy_engine.dart';
import '../data/api/api_failure.dart';
import '../data/async_value.dart';
import '../data/models/account_snapshot.dart';
import '../data/models/announcement.dart';
import '../data/models/chain_diversity.dart';
import '../data/models/entitlements.dart';
import '../data/models/plan.dart';
import '../data/models/proxy.dart';
import '../data/models/proxy_chain.dart';
import '../data/models/proxy_credentials.dart';
import '../data/models/resolved_chain.dart';
import '../data/models/saved_chain.dart';
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

/// What the app is doing between "user tapped Connect" and "the engine has
/// the chain".
///
/// These stages exist because a real connect is no longer one call. It is:
/// save the chain → resolve it → fetch credentials one hop at a time → start
/// the engine. Each can fail differently and each takes real time, so the UI
/// names the step rather than showing an undifferentiated spinner.
enum ConnectStage {
  idle,

  /// `POST`/`PATCH /api/chains/` — the chain must exist server-side before
  /// it can be resolved.
  savingChain,

  /// `POST /api/chains/{id}/resolve/`.
  resolving,

  /// One `GET /api/proxies/{id}/` per hop, under the 60/hour throttle.
  fetchingCredentials,

  /// Handed to the engine; `EngineStatus` takes over from here.
  startingEngine,
}

/// Single app-wide store for catalogue state, the chain draft and the engine
/// mirror.
///
/// A `ChangeNotifier` behind an `InheritedNotifier` is still enough at this
/// size. What changed with the real API is that failures are now typed:
/// every async surface carries the [ApiFailure] that produced it, so a
/// screen can branch on `chain_length_exceeded` or a 429 rather than
/// matching on message text.
class AppController extends ChangeNotifier {
  AppController({required this.engine, required this.repository}) {
    _statusSub = engine.statusStream.listen((status) {
      final previous = _status;
      _status = status;
      _onEngineStatus(previous, status);
      notifyListeners();
    });
    _statsSub = engine.statsStream.listen((stats) {
      _stats = stats;
      _rememberStats(stats);
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

  DateTime? _sessionStartedAt;
  ThroughputStats _lastLiveStats = ThroughputStats.zero;

  // --- Catalogue ---------------------------------------------------------
  AsyncValue<List<Proxy>> _proxies = const AsyncValue.loading();
  AsyncValue<List<Proxy>> get proxies => _proxies;

  AsyncValue<AccountSnapshot> _account = const AsyncValue.loading();
  AsyncValue<AccountSnapshot> get account => _account;

  /// Kept so the paywall and the connection header read exactly as before.
  AsyncValue<List<Plan>> get plans => _account.map(
    loading: () => const AsyncValue<List<Plan>>.loading(),
    data: (snapshot) => AsyncValue<List<Plan>>.data(snapshot.availablePlans),
    error: (message, detail, failure) =>
        AsyncValue<List<Plan>>.error(message, detail: detail, failure: failure),
  );

  AsyncValue<Subscription> get subscription => _account.map(
    loading: () => const AsyncValue<Subscription>.loading(),
    data: (snapshot) => AsyncValue<Subscription>.data(snapshot.subscription),
    error: (message, detail, failure) => AsyncValue<Subscription>.error(
      message,
      detail: detail,
      failure: failure,
    ),
  );

  Entitlements get entitlements =>
      _account.valueOrNull?.entitlements ?? Entitlements.unknown;

  List<Announcement> _announcements = const <Announcement>[];
  List<Announcement> get announcements => _announcements;

  final Set<String> _dismissedAnnouncements = <String>{};

  List<Announcement> get visibleAnnouncements => _announcements
      .where((a) => !_dismissedAnnouncements.contains(a.key))
      .toList(growable: false);

  void dismissAnnouncement(Announcement announcement) {
    _dismissedAnnouncements.add(announcement.key);
    notifyListeners();
  }

  // --- Chain draft -------------------------------------------------------
  ProxyChain _chain = ProxyChain.empty(name: 'My route');
  ProxyChain get chain => _chain;

  /// Server id of the saved chain this draft corresponds to, or null when
  /// the draft has never been persisted (or has been edited since).
  int? _savedChainId;
  int? get savedChainId => _savedChainId;

  // --- Diversity (R2) ----------------------------------------------------
  AsyncValue<ChainDiversityReport>? _diversity;

  /// Null when there is nothing to analyse (an empty chain).
  AsyncValue<ChainDiversityReport>? get diversity => _diversity;

  Timer? _diversityDebounce;
  int _diversityRequest = 0;

  // --- Connect sequence --------------------------------------------------
  ConnectStage _connectStage = ConnectStage.idle;
  ConnectStage get connectStage => _connectStage;

  ApiFailure? _connectFailure;

  /// The API failure that stopped a connect before the engine was reached.
  /// Distinct from [EngineStatus.failure], which is the engine's own.
  ApiFailure? get connectFailure => _connectFailure;

  /// Progress through the per-hop credential fetch, e.g. "2 of 3".
  int _credentialsFetched = 0;
  int get credentialsFetched => _credentialsFetched;

  // --- Browser filters ---------------------------------------------------
  String _query = '';
  String get query => _query;

  ProxyProtocol? _protocolFilter;
  ProxyProtocol? get protocolFilter => _protocolFilter;

  bool _aliveOnly = false;
  bool get aliveOnly => _aliveOnly;

  /// Max hops the UI should allow.
  ///
  /// Taken from the server's resolved entitlements, not from the plan row:
  /// `billing.entitlements` is the single place that answers this, and the
  /// server re-checks it on every write regardless of what the client thinks
  /// (architecture.md §3).
  int get maxChainLength => entitlements.maxChainLength;

  bool get isFreeTier => entitlements.isFreeTier;

  /// True when the entitlement came from `settings.ENTITLEMENT_FALLBACK` —
  /// the server found no active free plan. An operator problem the user
  /// cannot fix by upgrading, so it is worth saying so.
  bool get isEntitlementFallback => entitlements.isFallback;

  bool get allowsPremium => entitlements.allowsTier('premium');

  // -----------------------------------------------------------------------
  // Loading
  // -----------------------------------------------------------------------

  Future<void> load() async {
    _proxies = const AsyncValue.loading();
    _account = const AsyncValue.loading();
    notifyListeners();

    // The account comes first and is awaited: entitlements decide how many
    // hops the chain may hold, and seeding a chain before they are known
    // would build a route the plan does not allow and then trim it on
    // screen.
    await _loadAccount();
    await Future.wait<void>(<Future<void>>[
      _loadProxies(),
      _loadAnnouncements(),
    ]);
  }

  Future<void> _loadProxies() async {
    try {
      final result = await repository.fetchProxies();
      _proxies = AsyncValue<List<Proxy>>.data(result);
    } on ApiFailure catch (error) {
      _proxies = AsyncValue<List<Proxy>>.error(
        _headline(error, 'Could not load the proxy list.'),
        detail: error.detailLine,
        failure: error,
      );
    } on Object catch (error) {
      _proxies = AsyncValue<List<Proxy>>.error(
        'Could not load the proxy list.',
        detail: error.toString(),
      );
    }
    notifyListeners();
  }

  Future<void> _loadAccount() async {
    try {
      _account = AsyncValue<AccountSnapshot>.data(
        await repository.fetchAccount(),
      );
    } on ApiFailure catch (error) {
      _account = AsyncValue<AccountSnapshot>.error(
        _headline(error, 'Could not confirm your plan.'),
        detail: error.detailLine,
        failure: error,
      );
    } on Object catch (error) {
      _account = AsyncValue<AccountSnapshot>.error(
        'Could not confirm your plan.',
        detail: error.toString(),
      );
    }
    notifyListeners();
  }

  Future<void> _loadAnnouncements() async {
    try {
      _announcements = await repository.fetchAnnouncements();
    } on ApiFailure {
      // Operator copy is never load-bearing. A failure here must not turn
      // into an error state over the whole screen.
      _announcements = const <Announcement>[];
    }
    notifyListeners();
  }

  Future<void> refreshProxies() => _loadProxies();

  Future<void> refreshAccount() => _loadAccount();

  /// Full reload, for the pull-to-refresh and the error-state retry.
  Future<void> reload() => load();

  /// Drops every trace of the previous account. Wired to sign-out so one
  /// user's inventory never shows under another's session.
  void resetForSignOut() {
    _diversityDebounce?.cancel();
    _proxies = const AsyncValue.loading();
    _account = const AsyncValue.loading();
    _announcements = const <Announcement>[];
    _dismissedAnnouncements.clear();
    _chain = ProxyChain.empty(name: 'My route');
    _savedChainId = null;
    _diversity = null;
    _connectStage = ConnectStage.idle;
    _connectFailure = null;
    _credentialsFetched = 0;
    _query = '';
    _protocolFilter = null;
    _aliveOnly = false;
    notifyListeners();
  }

  /// A headline sentence for an error surface.
  ///
  /// A 429 keeps the server's own wording, which names the wait; everything
  /// else gets the screen's own framing so the user is told what failed
  /// rather than what the HTTP layer saw.
  static String _headline(ApiFailure error, String fallback) {
    if (error is ApiException && error.isThrottled) return error.message;
    if (error.isOffline) return error.message;
    return fallback;
  }

  // -----------------------------------------------------------------------
  // Filtering
  // -----------------------------------------------------------------------

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

  /// Filtered view of the pool. Computed on demand; the list is rendered
  /// with `ListView.builder`, so only the visible slice is laid out.
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

  bool isInChain(Proxy proxy) => _chain.hops.any((h) => h.proxy.id == proxy.id);

  // -----------------------------------------------------------------------
  // Chain editing
  // -----------------------------------------------------------------------

  /// Returns null on success, or the reason it was refused.
  ///
  /// Every check here is also enforced server-side on `POST /api/chains/`.
  /// This exists so the control is disabled before the tap, not as a
  /// security boundary — a client-only check gets bypassed within a week of
  /// launch.
  HopRejection? addHop(Proxy proxy) {
    if (_status.isConnected || _status.isBusy) return HopRejection.engineBusy;
    if (isInChain(proxy)) return HopRejection.duplicate;
    if (proxy.isPremium && !allowsPremium) return HopRejection.premiumRequired;
    if (_chain.length >= maxChainLength) return HopRejection.chainLengthLimit;

    _chain = _chain.withHopAdded(proxy);
    _onChainEdited();
    return null;
  }

  void removeHopAt(int index) {
    if (_status.isConnected || _status.isBusy) return;
    _chain = _chain.withHopRemovedAt(index);
    _onChainEdited();
  }

  void moveHop(int oldIndex, int newIndex) {
    if (_status.isConnected || _status.isBusy) return;
    _chain = _chain.withHopMoved(oldIndex, newIndex);
    _onChainEdited();
  }

  void clearChain() {
    if (_status.isConnected || _status.isBusy) return;
    _chain = ProxyChain.empty(name: _chain.name);
    _onChainEdited();
  }

  void renameChain(String name) {
    _chain = _chain.copyWith(name: name);
    notifyListeners();
  }

  /// Any structural edit invalidates both the saved copy and the analysis.
  void _onChainEdited() {
    // The server copy no longer matches the draft. Clearing the id makes
    // the next connect re-save rather than resolve a stale chain — which
    // would silently connect through hops the user just removed.
    _savedChainId = null;
    _connectFailure = null;
    notifyListeners();
    _scheduleDiversity();
  }

  // -----------------------------------------------------------------------
  // R2 — diversity
  // -----------------------------------------------------------------------

  /// Re-analyses the draft chain after a short debounce.
  ///
  /// `POST /api/chains/diversity/` reads cached intel and never performs a
  /// DNS lookup — that is why the backend made the saved-chain variant a GET
  /// — so this is cheap. It is still debounced and bounded by the 240/hour
  /// `chain_diversity` scope, and it is **not** triggered by rendering a
  /// screen: only by an actual edit to the chain.
  void _scheduleDiversity() {
    _diversityDebounce?.cancel();
    final proxyIds = _chain.hops
        .map((h) => h.proxy.id)
        .toList(growable: false);
    if (proxyIds.isEmpty) {
      _diversity = null;
      notifyListeners();
      return;
    }
    _diversity = const AsyncValue<ChainDiversityReport>.loading();
    notifyListeners();
    _diversityDebounce = Timer(
      const Duration(milliseconds: 400),
      () => refreshDiversity(),
    );
  }

  Future<void> refreshDiversity() async {
    final proxyIds = _chain.hops
        .map((h) => h.proxy.id)
        .toList(growable: false);
    if (proxyIds.isEmpty) {
      _diversity = null;
      notifyListeners();
      return;
    }
    final request = ++_diversityRequest;
    _diversity = const AsyncValue<ChainDiversityReport>.loading();
    notifyListeners();
    try {
      final report = await repository.previewDiversity(proxyIds);
      // A later edit already superseded this request. Dropping it keeps the
      // panel from flashing an analysis of a chain the user no longer has.
      if (request != _diversityRequest) return;
      _diversity = AsyncValue<ChainDiversityReport>.data(report);
    } on ApiFailure catch (error) {
      if (request != _diversityRequest) return;
      _diversity = AsyncValue<ChainDiversityReport>.error(
        _headline(error, 'Could not check this chain.'),
        detail: error.detailLine,
        failure: error,
      );
    }
    notifyListeners();
  }

  // -----------------------------------------------------------------------
  // Connect
  // -----------------------------------------------------------------------

  void _setStage(ConnectStage stage) {
    _connectStage = stage;
    notifyListeners();
  }

  /// The real connect sequence.
  ///
  /// 1. **Save** the draft, because `/resolve/` operates on a saved chain.
  /// 2. **Resolve** it — fixed hops come back as themselves, constrained
  ///    hops as whichever healthy, entitled proxy currently scores highest.
  /// 3. **Fetch credentials**, one `GET /api/proxies/{id}/` per hop (D18).
  ///    A three-hop connect spends three of the 60/hour budget, and each
  ///    call is audit-logged. This is the only place in the app that calls
  ///    it.
  /// 4. **Start the engine**, handing it the credentials and nothing else
  ///    keeping a reference to them.
  ///
  /// The credentials live in one local variable for the duration of this
  /// method. They are not stored on the controller, not written to secure
  /// storage, and not logged.
  Future<void> connect() async {
    if (_status.isBusy || _status.isConnected) return;
    if (_chain.isEmpty) return;

    _connectFailure = null;
    _credentialsFetched = 0;

    ChainCredentials? credentials;
    try {
      // Connecting saves nothing. Previously this created a chain first,
      // which spent the write budget, left a preset the user never asked
      // for, and — because the name is reused — failed outright on the
      // SECOND connect with "You already have a chain with this name",
      // making the app impossible to connect with twice.
      _setStage(ConnectStage.resolving);
      final resolved = await repository.resolveProxies(
        _chain.hops.map((h) => h.proxy.id).toList(growable: false),
      );

      _setStage(ConnectStage.fetchingCredentials);
      credentials = await _fetchCredentials(resolved);

      _setStage(ConnectStage.startingEngine);
      await engine.connect(_chain, credentials: credentials);
    } on ApiFailure catch (error) {
      _connectFailure = error;
      _setStage(ConnectStage.idle);
      return;
    } on EngineException {
      // Already published on statusStream; the UI renders it from there.
      _setStage(ConnectStage.idle);
      return;
    } finally {
      // Drop the only reference this method holds. Nothing above it kept
      // one, so the credentials become unreachable the moment the engine is
      // done with them.
      credentials = null;
      if (_connectStage == ConnectStage.startingEngine) {
        _setStage(ConnectStage.idle);
      }
    }
    _setStage(ConnectStage.idle);
  }

  Future<ChainCredentials> _fetchCredentials(ResolvedChain resolved) async {
    _credentialsFetched = 0;
    notifyListeners();
    final credentials = await repository.fetchChainCredentials(resolved);
    _credentialsFetched = credentials.length;
    notifyListeners();
    return credentials;
  }

  Future<void> disconnect() => engine.disconnect();

  Future<void> toggleConnection() async {
    if (_status.isBusy || _connectStage != ConnectStage.idle) return;
    if (_status.isConnected) {
      await disconnect();
    } else {
      await connect();
    }
  }

  /// Session accounting.
  ///
  /// `POST /api/usage/` is fired once, on the transition out of
  /// `connected` — not on a timer, and not per stats tick, which would spend
  /// the 120/hour budget in a minute. A failure is swallowed: the user's
  /// session already ended, and an error toast about telemetry after a
  /// disconnect is noise.
  void _onEngineStatus(EngineStatus previous, EngineStatus next) {
    if (next.isConnected && !previous.isConnected) {
      _sessionStartedAt = next.connectedAt ?? DateTime.now();
      _lastLiveStats = ThroughputStats.zero;
      return;
    }
    if (previous.isConnected && !next.isConnected) {
      final started = _sessionStartedAt;
      _sessionStartedAt = null;
      if (started == null) return;
      final stats = _lastLiveStats.bytesDown > 0 || _lastLiveStats.bytesUp > 0
          ? _lastLiveStats
          : _stats;
      unawaited(
        repository
            .reportUsage(
              bytesUp: stats.bytesUp,
              bytesDown: stats.bytesDown,
              sessionStart: started,
              sessionEnd: DateTime.now(),
            )
            .catchError((Object _) {}),
      );
    }
  }

  /// Keeps the last non-zero stats sample, because the engine resets the
  /// counters to zero as part of tearing down — by the time the status says
  /// `disconnected`, the totals to report are already gone.
  void _rememberStats(ThroughputStats stats) {
    if (stats.bytesDown > 0 || stats.bytesUp > 0) _lastLiveStats = stats;
  }

  // -----------------------------------------------------------------------
  // Saved chains
  // -----------------------------------------------------------------------

  /// Loads a saved chain into the builder.
  ///
  /// Returns false when the chain has constrained hops. The builder can only
  /// express pinned hops today, so rather than show a half-populated route —
  /// or invent a placeholder proxy for a slot — it says so and leaves the
  /// draft alone.
  bool adoptSavedChain(SavedChain saved) {
    final editable = saved.toEditableChain();
    if (editable == null) return false;
    _chain = editable;
    _savedChainId = saved.id;
    notifyListeners();
    _scheduleDiversity();
    return true;
  }

  // -----------------------------------------------------------------------
  // Billing
  // -----------------------------------------------------------------------

  /// Stand-in for Google Play Billing.
  ///
  /// ⚠️ **Client-side only, and deliberately not wired to the API.** There
  /// is no purchase endpoint on `/api/` yet, and inventing one here would
  /// put an entitlement decision on the client, which architecture.md §4
  /// forbids ("validate purchases server-side; never trust the client").
  /// This only demonstrates the paywall flow; the real version will send a
  /// Play purchase token and re-read `/api/subscription/`.
  Future<void> simulatePurchase(Plan plan) async {
    final snapshot = _account.valueOrNull;
    if (snapshot == null) return;
    _account = const AsyncValue.loading();
    notifyListeners();
    await Future<void>.delayed(const Duration(milliseconds: 650));
    _account = AsyncValue<AccountSnapshot>.data(
      AccountSnapshot(
        plan: plan,
        subscription: snapshot.subscription.copyWith(
          plan: plan,
          status: SubscriptionStatus.active,
          expiresAt: DateTime.now().add(Duration(days: plan.durationDays)),
          bytesUsed: plan.isFreeTier ? snapshot.subscription.bytesUsed : 0,
        ),
        entitlements: snapshot.entitlements,
        availablePlans: snapshot.availablePlans,
        hasSubscriptionRow: true,
      ),
    );
    notifyListeners();
  }

  @override
  void dispose() {
    _diversityDebounce?.cancel();
    _statusSub?.cancel();
    _statsSub?.cancel();
    engine.dispose();
    super.dispose();
  }
}
