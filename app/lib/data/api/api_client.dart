import 'dart:async';

import '../models/account_snapshot.dart';
import '../models/announcement.dart';
import '../models/auth_session.dart';
import '../models/chain_diversity.dart';
import '../models/paged.dart';
import '../models/proxy.dart';
import '../models/proxy_credentials.dart';
import '../models/resolved_chain.dart';
import '../models/saved_chain.dart';
import 'api_config.dart';
import 'api_failure.dart';
import 'transport.dart';

/// Typed access to `/api/`.
///
/// One method per endpoint, each returning a model rather than a `Map`.
/// Every failure leaves here as an [ApiFailure]; nothing above this layer
/// touches JSON, HTTP status codes or `dart:io` exceptions.
///
/// The surface mirrors `api/urls.py`:
///
/// ```
/// POST /api/auth/register/   /api/auth/login/   /api/auth/refresh/
/// GET  /api/proxies/         GET  /api/proxies/{id}/      (credentials)
/// GET  /api/chains/          POST /api/chains/            (+ detail CRUD)
/// POST /api/chains/{id}/resolve/
/// GET  /api/chains/{id}/diversity/   POST /api/chains/diversity/
/// GET  /api/subscription/
/// GET  /api/content/announcements/
/// POST /api/usage/
/// ```
class ApiClient {
  ApiClient({required this.transport, required this.config});

  final Transport transport;
  final ApiConfig config;

  // -- auth ---------------------------------------------------------------

  /// `POST /api/auth/login/`.
  ///
  /// Throttled at 20/hour. A wrong password comes back as a 401 with
  /// `no_active_account`; the message is deliberately identical whether or
  /// not the account exists, so the UI must not try to say which.
  Future<AuthSession> login({
    required String email,
    required String password,
  }) async {
    final body = await _json(
      RequestSpec(
        method: HttpMethod.post,
        path: '/api/auth/login/',
        auth: false,
        body: <String, dynamic>{
          'email': email.trim().toLowerCase(),
          'password': password,
        },
      ),
    );
    return AuthSession.fromJson(body);
  }

  /// `POST /api/auth/register/`. Throttled at 10/hour.
  ///
  /// Returns a session directly — `RegisterSerializer.to_representation`
  /// mints the token pair, so there is no second login round trip.
  Future<AuthSession> register({
    required String email,
    required String password,
    required String passwordConfirm,
    String firstName = '',
    String lastName = '',
  }) async {
    final body = await _json(
      RequestSpec(
        method: HttpMethod.post,
        path: '/api/auth/register/',
        auth: false,
        body: <String, dynamic>{
          'email': email.trim().toLowerCase(),
          'password': password,
          'password_confirm': passwordConfirm,
          if (firstName.isNotEmpty) 'first_name': firstName,
          if (lastName.isNotEmpty) 'last_name': lastName,
        },
      ),
    );
    return AuthSession.fromJson(body);
  }

  // -- proxies ------------------------------------------------------------

  /// `GET /api/proxies/` — already entitlement-scoped by the server.
  ///
  /// Default ordering is `-quality_score`: best-first, not fastest-first.
  /// That is not the same list, and the difference is the whole point of the
  /// R1 scoring model (D13).
  Future<Paged<Proxy>> fetchProxies({
    int page = 1,
    int pageSize = 100,
    String? country,
    String? protocol,
    String? tier,
    bool? isAlive,
    int? maxP95LatencyMs,
    double? minSuccessRate,
    double? minQualityScore,
    String? search,
    String ordering = '-quality_score',
  }) async {
    final body = await transport.send(
      RequestSpec(
        method: HttpMethod.get,
        path: '/api/proxies/',
        query: <String, Object?>{
          'page': page,
          // 100 is `max_page_size`; asking for more is silently clamped.
          'page_size': pageSize,
          'country': country,
          'protocol': protocol,
          'tier': tier,
          'is_alive': isAlive,
          'max_p95_latency_ms': maxP95LatencyMs,
          'min_success_rate': minSuccessRate,
          'min_quality_score': minQualityScore,
          'search': search,
          'ordering': ordering,
        },
      ),
    );
    return Paged<Proxy>.fromJson(body, Proxy.fromJson);
  }

  /// Walks every page of the proxy list.
  ///
  /// The inventory is bounded (one operator's purchased proxies, not the
  /// internet), and the browser filters and sorts client-side, so holding
  /// the whole entitled set is the right trade. [maxPages] stops a
  /// pathological inventory from pinning the main isolate; the list simply
  /// ends early and the count says what was skipped.
  Future<List<Proxy>> fetchAllProxies({int maxPages = 20}) async {
    final all = <Proxy>[];
    for (var page = 1; page <= maxPages; page++) {
      final chunk = await fetchProxies(page: page);
      all.addAll(chunk.results);
      if (!chunk.hasMore) break;
    }
    return all;
  }

  /// `GET /api/proxies/{id}/` — **the credential disclosure**.
  ///
  /// Throttled at **60/hour** and audit-logged per call (D19). Call this at
  /// connect time and nowhere else: not on list render, not on screen focus,
  /// not speculatively. A three-hop connect spends three of sixty.
  ///
  /// [url] is the absolute `credentials_url` the resolver handed back. Its
  /// **path is re-based onto this client's own base URL**: the server builds
  /// it with `request.build_absolute_uri`, so behind a reverse proxy — or
  /// any deployment where the `Host` header is not what the device dialled —
  /// it can name an origin the device cannot reach. The path is the part
  /// that is authoritative.
  Future<ProxyCredentials> fetchCredentials({int? proxyId, String? url}) async {
    assert(
      proxyId != null || url != null,
      'fetchCredentials needs a proxy id or a credentials_url.',
    );
    final path = url != null && url.isNotEmpty
        ? _pathOf(url)
        : '/api/proxies/$proxyId/';
    final body = await _json(
      RequestSpec(method: HttpMethod.get, path: path),
    );
    return ProxyCredentials.fromJson(body);
  }

  /// Keeps only the path+query of an absolute URL the server generated, so a
  /// `credentials_url` of `http://127.0.0.1:8000/api/proxies/11/` still
  /// works from an emulator that reaches the host at `10.0.2.2`.
  static String _pathOf(String url) {
    final uri = Uri.tryParse(url);
    if (uri == null || !uri.hasScheme) return url;
    return uri.hasQuery ? '${uri.path}?${uri.query}' : uri.path;
  }

  // -- chains -------------------------------------------------------------

  /// `GET /api/chains/`.
  Future<List<SavedChain>> fetchChains() async {
    final body = await transport.send(
      RequestSpec(
        method: HttpMethod.get,
        path: '/api/chains/',
        query: const <String, Object?>{'page_size': 100},
      ),
    );
    return Paged<SavedChain>.fromJson(body, SavedChain.fromJson).results;
  }

  /// `POST /api/chains/` with the Phase 1 `proxy_ids` shape.
  ///
  /// Throws an [ApiException] with `chain_length_exceeded` or
  /// `proxy_not_entitled` when the plan does not cover the chain — both
  /// carry the real limits as numbers in `detail`, not as prose.
  Future<SavedChain> createChain({
    required String name,
    required List<int> proxyIds,
  }) async {
    final body = await _json(
      RequestSpec(
        method: HttpMethod.post,
        path: '/api/chains/',
        body: <String, dynamic>{'name': name, 'proxy_ids': proxyIds},
      ),
    );
    return SavedChain.fromJson(body);
  }

  /// `PATCH /api/chains/{id}/`.
  Future<SavedChain> updateChain({
    required int id,
    String? name,
    List<int>? proxyIds,
  }) async {
    final payload = <String, dynamic>{};
    if (name != null) payload['name'] = name;
    if (proxyIds != null) payload['proxy_ids'] = proxyIds;
    final body = await _json(
      RequestSpec(
        method: HttpMethod.patch,
        path: '/api/chains/$id/',
        body: payload,
      ),
    );
    return SavedChain.fromJson(body);
  }

  /// `DELETE /api/chains/{id}/`.
  Future<void> deleteChain(int id) async {
    await transport.send(
      RequestSpec(method: HttpMethod.delete, path: '/api/chains/$id/'),
    );
  }

  /// `POST /api/chains/{id}/resolve/` — concrete proxies, right now.
  ///
  /// POST rather than GET because the answer is a point-in-time decision:
  /// two calls a minute apart can legitimately return different proxies, and
  /// anything that cached this as a GET would pin a chain to a dead hop.
  ///
  /// Fails with 409 `chain_unresolvable` when the inventory cannot satisfy a
  /// slot — the request is fine, the world is not — and with 403
  /// `proxy_not_entitled` when a subscription expired since the chain was
  /// saved. Throttled at 120/hour: a client resolves on every connect and
  /// every failover.
  Future<ResolvedChain> resolveChain(int chainId) async {
    final body = await _json(
      RequestSpec(
        method: HttpMethod.post,
        path: '/api/chains/$chainId/resolve/',
      ),
    );
    return ResolvedChain.fromJson(body);
  }

  /// `POST /api/chains/resolve/` — resolve without saving anything.
  ///
  /// Connecting should not persist a preset. Saving first spent the
  /// chain-write budget, left a saved chain the user never asked for, and —
  /// because the client reused one name — hit the per-user unique-name
  /// constraint on the *second* connect, which made connecting impossible
  /// until the chain was renamed. This path writes nothing.
  ///
  /// Goes through the same resolver as the saved path, so a preview can
  /// never disagree with the real thing.
  Future<ResolvedChain> resolveProxies(List<int> proxyIds) async {
    final body = await _json(
      RequestSpec(
        method: HttpMethod.post,
        path: '/api/chains/resolve/',
        body: <String, Object?>{'proxy_ids': proxyIds},
      ),
    );
    return ResolvedChain.fromJson(body);
  }

  /// `GET /api/chains/{id}/diversity/` — the R2 report for a saved chain.
  ///
  /// Reads cached intel and never performs a lookup, which is why it is a
  /// GET: a client must not be able to make the server emit DNS by opening
  /// a screen.
  Future<ChainDiversityReport> fetchChainDiversity(int chainId) async {
    final body = await _json(
      RequestSpec(
        method: HttpMethod.get,
        path: '/api/chains/$chainId/diversity/',
      ),
    );
    return ChainDiversityReport.fromJson(body);
  }

  /// `POST /api/chains/diversity/` — score a chain **before** saving it.
  ///
  /// The whole value of the warning is that it arrives while the user is
  /// still assembling the chain. The ids are deliberately **not**
  /// de-duplicated: a chain listing the same proxy twice is exactly the
  /// mistake this feature exists to catch.
  Future<ChainDiversityReport> previewDiversity(List<int> proxyIds) async {
    final body = await _json(
      RequestSpec(
        method: HttpMethod.post,
        path: '/api/chains/diversity/',
        body: <String, dynamic>{'proxy_ids': proxyIds},
      ),
    );
    return ChainDiversityReport.fromJson(body);
  }

  // -- subscription -------------------------------------------------------

  /// `GET /api/subscription/` — plan, subscription, entitlements and the
  /// plan catalogue, in one response.
  Future<AccountSnapshot> fetchAccount() async {
    final body = await _json(
      RequestSpec(method: HttpMethod.get, path: '/api/subscription/'),
    );
    return AccountSnapshot.fromJson(body);
  }

  // -- content ------------------------------------------------------------

  /// `GET /api/content/announcements/`. Throttled at 60/hour, so this is
  /// fetched once per app start, not per screen build.
  Future<List<Announcement>> fetchAnnouncements() async {
    final body = await transport.send(
      RequestSpec(
        method: HttpMethod.get,
        path: '/api/content/announcements/',
        // Public endpoint: `AllowAny`. Still sent with the token when one
        // exists, which costs nothing and keeps the throttle per-user
        // rather than per-IP.
        query: const <String, Object?>{'page_size': 20},
      ),
    );
    final page = Paged<Announcement>.fromJson(body, Announcement.fromJson);
    final sorted = List<Announcement>.of(page.results)
      ..sort((a, b) => b.priority.compareTo(a.priority));
    return sorted;
  }

  // -- usage --------------------------------------------------------------

  /// `POST /api/usage/` — report a finished session.
  ///
  /// The row is attributed to the token's user; a `user` field in the body
  /// is ignored. Byte counts are untrusted input used for accounting only,
  /// never to grant access. Throttled at 120/hour.
  Future<void> reportUsage({
    required int bytesUp,
    required int bytesDown,
    required DateTime sessionStart,
    required DateTime sessionEnd,
  }) async {
    await transport.send(
      RequestSpec(
        method: HttpMethod.post,
        path: '/api/usage/',
        body: <String, dynamic>{
          'bytes_up': bytesUp,
          'bytes_down': bytesDown,
          // The serializer rejects a start or end more than five minutes in
          // the future, so always send UTC — a device whose clock is ahead
          // in local time would otherwise fail validation.
          'session_start': sessionStart.toUtc().toIso8601String(),
          'session_end': sessionEnd.toUtc().toIso8601String(),
        },
      ),
    );
  }

  // -- plumbing -----------------------------------------------------------

  Future<Map<String, dynamic>> _json(RequestSpec spec) async {
    final body = await transport.send(spec);
    if (body is Map<String, dynamic>) return body;
    throw MalformedResponseFailure(
      'The server returned an unexpected response for ${spec.path}.',
      snippet: body == null ? 'Empty body.' : '${body.runtimeType}',
    );
  }

  void close() => transport.close();
}
