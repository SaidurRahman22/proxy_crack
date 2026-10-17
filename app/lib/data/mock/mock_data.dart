import 'dart:math';

import '../models/account_snapshot.dart';
import '../models/announcement.dart';
import '../models/chain_diversity.dart';
import '../models/chain_hop.dart';
import '../models/entitlements.dart';
import '../models/plan.dart';
import '../models/proxy.dart';
import '../models/proxy_chain.dart';
import '../models/resolved_chain.dart';
import '../models/saved_chain.dart';
import '../models/subscription.dart';

class _Region {
  const _Region(this.code, this.country, this.cities, this.baseLatency);
  final String code;
  final String country;
  final List<String> cities;
  final int baseLatency;
}

/// Deterministic sample data. Replaced by the DRF client in `data/` once the
/// backend lands; nothing in `features/` knows the difference.
abstract final class MockData {
  static const List<_Region> _regions = <_Region>[
    _Region('NL', 'Netherlands', <String>['Amsterdam', 'Rotterdam'], 38),
    _Region('DE', 'Germany', <String>['Frankfurt', 'Berlin', 'Munich'], 44),
    _Region('FR', 'France', <String>['Paris', 'Marseille'], 52),
    _Region('GB', 'United Kingdom', <String>['London', 'Manchester'], 48),
    _Region('SE', 'Sweden', <String>['Stockholm'], 61),
    _Region('CH', 'Switzerland', <String>['Zurich', 'Geneva'], 55),
    _Region('IS', 'Iceland', <String>['Reykjavik'], 74),
    _Region('RO', 'Romania', <String>['Bucharest'], 67),
    _Region('US', 'United States', <String>['Ashburn', 'Dallas', 'Seattle'], 96),
    _Region('CA', 'Canada', <String>['Toronto', 'Montreal'], 88),
    _Region('BR', 'Brazil', <String>['Sao Paulo'], 142),
    _Region('SG', 'Singapore', <String>['Singapore'], 168),
    _Region('JP', 'Japan', <String>['Tokyo', 'Osaka'], 184),
    _Region('KR', 'South Korea', <String>['Seoul'], 191),
    _Region('IN', 'India', <String>['Mumbai', 'Chennai'], 156),
    _Region('AU', 'Australia', <String>['Sydney', 'Melbourne'], 238),
    _Region('ZA', 'South Africa', <String>['Johannesburg'], 212),
    _Region('AE', 'UAE', <String>['Dubai'], 149),
    _Region('TR', 'Turkey', <String>['Istanbul'], 108),
    _Region('PL', 'Poland', <String>['Warsaw'], 63),
  ];

  /// 280 rows: enough that a naive `ListView(children: [...])` would stutter,
  /// which is exactly why the browser uses `ListView.builder`.
  static List<Proxy> proxies({int count = 280}) {
    final rng = Random(2026);
    final now = DateTime.now();
    return List<Proxy>.generate(count, (i) {
      final region = _regions[i % _regions.length];
      final city = region.cities[rng.nextInt(region.cities.length)];
      final protocol =
          ProxyProtocol.values[(i ~/ _regions.length) % ProxyProtocol.values.length];
      // Roughly a third premium, deterministically spread.
      final premium = i % 3 == 1;
      // Roughly one in eleven is dead -- the health checker finds these.
      final alive = i % 11 != 4;
      final latency =
          region.baseLatency + rng.nextInt(55) - (premium ? 14 : 0);
      // Roughly one in seventeen has never been probed, so the UI's
      // "unknown is not a pass" path is exercised in mock mode too (D15).
      final unprobed = i % 17 == 3;
      final probeCount = unprobed ? 0 : 8 + rng.nextInt(180);
      final successRate = unprobed
          ? null
          : alive
              ? 0.82 + rng.nextDouble() * 0.18
              : rng.nextDouble() * 0.35;
      final p95 = unprobed || !alive
          ? null
          : (latency * (1.25 + rng.nextDouble() * 0.6)).round();

      return Proxy(
        id: 1000 + i,
        protocol: protocol,
        host: '${region.code.toLowerCase()}-'
            '${(i % 97 + 1).toString().padLeft(2, '0')}.'
            '${premium ? 'premium' : 'pool'}.proxycrack.net',
        port: switch (protocol) {
          ProxyProtocol.shadowsocks => 8388 + (i % 12),
          ProxyProtocol.socks5 => 1080 + (i % 20),
          ProxyProtocol.http => 3128 + (i % 8),
        },
        country: region.country,
        countryCode: region.code,
        city: city,
        tier: premium ? ProxyTier.premium : ProxyTier.free,
        isAlive: alive,
        latencyMs: alive ? latency.clamp(18, 600) : 0,
        lastChecked: now.subtract(Duration(minutes: 2 + rng.nextInt(160))),
        successRate: successRate,
        probeCount: probeCount,
        consecutiveFailures: alive ? 0 : 3 + rng.nextInt(4),
        latencyP50Ms: p95 == null ? null : (p95 * 0.7).round(),
        latencyP95Ms: p95,
        qualityScore: unprobed
            ? Proxy.unprobedQualityScore
            : (successRate! * 100 * (alive ? 1 : 0.4)).clamp(0, 100),
        requiresCredentials: premium,
        uploadBatchId: 40 + (i ~/ 60),
      );
    }, growable: false);
  }

  static const Plan freePlan = Plan(
    id: 1,
    code: 'free',
    name: 'Free',
    priceCents: 0,
    currency: 'USD',
    durationDays: 0,
    maxChainLength: 1,
    maxDevices: 1,
    bandwidthLimitBytes: 2 * 1024 * 1024 * 1024,
    isFreeTier: true,
    tagline: 'One hop, shared pool. Enough to see if this works for you.',
  );

  static const Plan proMonthly = Plan(
    id: 2,
    code: 'pro-monthly',
    maxProxyTier: 'premium',
    name: 'Pro',
    priceCents: 899,
    currency: 'USD',
    durationDays: 30,
    maxChainLength: 3,
    maxDevices: 3,
    bandwidthLimitBytes: 0,
    isFreeTier: false,
    tagline: 'Three-hop chains across the full premium pool.',
  );

  static const Plan proAnnual = Plan(
    id: 3,
    code: 'pro-annual',
    maxProxyTier: 'premium',
    name: 'Pro Annual',
    priceCents: 7188,
    currency: 'USD',
    durationDays: 365,
    maxChainLength: 5,
    maxDevices: 5,
    bandwidthLimitBytes: 0,
    isFreeTier: false,
    tagline: 'Same as Pro, two months free, five hops and five devices.',
  );

  static const List<Plan> plans = <Plan>[freePlan, proMonthly, proAnnual];

  /// The signed-in user starts on the free tier, so the paywall has a job.
  static Subscription get currentSubscription => Subscription(
        id: 501,
        userId: 1,
        plan: freePlan,
        startedAt: DateTime.now().subtract(const Duration(days: 12)),
        expiresAt: DateTime.now().add(const Duration(days: 3650)),
        status: SubscriptionStatus.active,
        bytesUsed: 1395864371,
      );

  /// What the mock `GET /api/subscription/` answers: a free-tier account
  /// with no subscription row, exactly as the real free tier behaves.
  static AccountSnapshot get account => AccountSnapshot(
        plan: freePlan,
        subscription: currentSubscription,
        entitlements: Entitlements(
          maxChainLength: freePlan.maxChainLength,
          maxDevices: freePlan.maxDevices,
          allowedProxyTiers: const <String>['free'],
          maxProxyTier: 'free',
          bandwidthLimitBytes: freePlan.bandwidthLimitBytes,
          bandwidthUsedBytes: currentSubscription.bytesUsed,
          bandwidthRemainingBytes:
              freePlan.bandwidthLimitBytes - currentSubscription.bytesUsed,
          isFreeTier: true,
          source: 'free_plan',
          bandwidthPeriodStart:
              DateTime.now().subtract(const Duration(days: 12)),
        ),
        availablePlans: plans,
      );

  static List<Announcement> get announcements => <Announcement>[
        Announcement(
          id: 1,
          key: 'notice.latency',
          kind: 'announcement',
          title: 'Chains trade speed for separation',
          body: 'Every hop adds a full round trip. A three-hop route '
              'routinely lands between 400 and 900 ms. That is the cost of '
              'the separation, not a fault.',
          priority: 10,
          publishedAt: DateTime.now().subtract(const Duration(days: 2)),
          updatedAt: DateTime.now().subtract(const Duration(days: 2)),
        ),
        Announcement(
          id: 2,
          key: 'notice.engine',
          kind: 'announcement',
          title: 'Running on the mock engine',
          body: 'No traffic is being routed. This build exercises the UI and '
              'the API client only.',
          priority: 5,
          publishedAt: DateTime.now().subtract(const Duration(days: 9)),
          updatedAt: DateTime.now().subtract(const Duration(days: 9)),
        ),
      ];

  /// Mirrors `ProxyChainSerializer` for the in-memory repository.
  static SavedChain savedChain({
    required int id,
    required String name,
    required List<int> proxyIds,
    required List<Proxy> pool,
  }) {
    final byId = <int, Proxy>{for (final proxy in pool) proxy.id: proxy};
    final hops = <SavedChainHop>[];
    for (var i = 0; i < proxyIds.length; i++) {
      final proxy = byId[proxyIds[i]];
      if (proxy == null) continue;
      hops.add(
        SavedChainHop(
          id: id * 100 + i,
          position: i,
          isConstrained: false,
          proxy: proxy,
        ),
      );
    }
    return SavedChain(
      id: id,
      name: name,
      hops: hops,
      length: hops.length,
      hasConstrainedHops: false,
      createdAt: DateTime.now(),
      updatedAt: DateTime.now(),
    );
  }

  /// Mirrors `ResolvedHopSerializer`, including the `credentials_url` that
  /// D18 makes the client fetch one hop at a time.
  static ResolvedChain resolve(SavedChain chain) => ResolvedChain(
        chainId: chain.id,
        chainName: chain.name,
        resolvedAt: DateTime.now(),
        hops: <ResolvedHop>[
          for (final hop in chain.hops)
            if (hop.proxy != null)
              ResolvedHop(
                position: hop.position,
                hopId: hop.id,
                fixed: true,
                proxy: hop.proxy!,
                credentialsUrl: '/api/proxies/${hop.proxy!.id}/',
                candidateCount: 1,
                failoverCapable: false,
              ),
        ],
      );

  /// Resolve an ad-hoc list of proxy ids, saving nothing — the mock twin of
  /// `POST /api/chains/resolve/`. `chainId` is null because no chain exists.
  static ResolvedChain resolveProxyIds(List<int> proxyIds) {
    final byId = <int, Proxy>{for (final p in proxies()) p.id: p};
    return ResolvedChain(
      // 0 / '' is what `ResolvedChain.fromJson` produces for the live
      // endpoint's `"chain": null`, so the mock must say the same thing —
      // `isSaved` is how either path is read.
      chainId: 0,
      chainName: '',
      resolvedAt: DateTime.now(),
      hops: <ResolvedHop>[
        for (var i = 0; i < proxyIds.length; i++)
          if (byId[proxyIds[i]] != null)
            ResolvedHop(
              position: i,
              hopId: 0,
              fixed: true,
              proxy: byId[proxyIds[i]]!,
              credentialsUrl: '/api/proxies/${proxyIds[i]}/',
              candidateCount: 1,
              failoverCapable: false,
            ),
      ],
    );
  }

  /// A plausible R2 report, including the `unknown` band — the mock must
  /// exercise D20's "unknown ranks above high" ordering, not just the happy
  /// path, or the chain builder's hardest case is never seen in a test.
  static ChainDiversityReport diversity(List<int> proxyIds, List<Proxy> pool) {
    final byId = <int, Proxy>{for (final proxy in pool) proxy.id: proxy};
    final chosen = proxyIds
        .map((id) => byId[id])
        .whereType<Proxy>()
        .toList(growable: false);

    final findings = <DiversityFinding>[];
    final countries = <String, List<int>>{};
    for (var i = 0; i < chosen.length; i++) {
      countries.putIfAbsent(chosen[i].countryCode, () => <int>[]).add(i);
    }
    for (final entry in countries.entries) {
      if (entry.value.length > 1) {
        findings.add(
          DiversityFinding(
            code: 'shared_asn_org',
            severity: DiversitySeverity.medium,
            title: 'Hops share a jurisdiction',
            message: 'Two or more hops sit in ${entry.key}.',
            positions: entry.value,
          ),
        );
      }
    }
    // Every third mock proxy has no cached intel, so the unknown band is
    // always represented on a chain of three or more.
    final unchecked = <int>[
      for (var i = 0; i < chosen.length; i++)
        if (chosen[i].id % 3 == 0) i,
    ];
    if (unchecked.isNotEmpty) {
      findings.add(
        DiversityFinding(
          code: 'hop_intel_unknown',
          severity: DiversitySeverity.unknown,
          title: 'Some hops could not be checked',
          message: 'No network record is cached for these hops, so their '
              'separation is unverified rather than confirmed.',
          positions: unchecked,
        ),
      );
    }

    final known = chosen.length - unchecked.length;
    final fraction = chosen.isEmpty ? 0.0 : known / chosen.length;
    final ceiling = 50 + 50 * fraction;
    final penalty = findings.length * 14.0;
    return ChainDiversityReport(
      score: (100 - penalty).clamp(0, ceiling).toDouble(),
      summary: findings.isEmpty
          ? 'No shared network, operator or vendor was found between these '
              'hops.'
          : 'Some hops could not be checked, so this chain\'s diversity is '
              'unverified rather than confirmed.',
      vocabularyNote: mockVocabularyNote,
      coverage: DiversityCoverage(
        hops: chosen.length,
        known: known,
        unknown: unchecked.length,
        fraction: fraction,
        scoreCeiling: ceiling,
      ),
      findings: findings,
      hops: <DiversityHop>[
        for (var i = 0; i < chosen.length; i++)
          DiversityHop(
            position: i,
            intelKnown: !unchecked.contains(i),
            intelStatus: unchecked.contains(i) ? 'no_record' : 'ok',
            proxyId: chosen[i].id,
            provider: 'Vendor ${String.fromCharCode(65 + (i % 3))}',
            asn: unchecked.contains(i) ? null : 64500 + i,
            country: chosen[i].countryCode,
          ),
      ],
      counts: <String, int>{
        'asns': known,
        'countries': countries.length,
        'providers': chosen.isEmpty ? 0 : 1 + (chosen.length - 1) % 3,
      },
      worstSeverity: findings.isEmpty
          ? null
          : findings
              .map((f) => f.severity)
              .reduce((a, b) => a.rank <= b.rank ? a : b),
      analysedAt: DateTime.now(),
    );
  }

  /// Copied verbatim from `proxies.diversity.VOCABULARY_NOTE` so the mock
  /// cannot quietly present a friendlier claim than the server does.
  static const String mockVocabularyNote =
      'This measures correlation resistance between the hops you chose -- '
      'whether they share a network, an operator or a jurisdiction. It is '
      'not anonymity. Commercial proxies bought under one account with one '
      'payment trail can still be correlated by timing, by transit '
      'providers, or by the vendors\' own records.';

  /// A pre-built 3-hop chain so the connection screen has something real to
  /// show on first run instead of an empty state.
  static ProxyChain defaultChain(List<Proxy> pool) {
    final healthy = pool.where((p) => p.isAlive).toList(growable: false);
    if (healthy.isEmpty) return ProxyChain.empty(name: 'Default route');

    Proxy pick(String code, ProxyProtocol protocol) {
      return healthy.firstWhere(
        (p) => p.countryCode == code && p.protocol == protocol,
        orElse: () => healthy.firstWhere(
          (p) => p.countryCode == code,
          orElse: () => healthy.first,
        ),
      );
    }

    final chosen = <Proxy>[
      pick('NL', ProxyProtocol.shadowsocks),
      pick('CH', ProxyProtocol.socks5),
      pick('SG', ProxyProtocol.http),
    ];

    return ProxyChain(
      id: 9001,
      userId: 1,
      name: 'Triple hop — EU to APAC',
      createdAt: DateTime.now().subtract(const Duration(days: 4)),
      hops: List<ChainHop>.generate(
        chosen.length,
        (i) => ChainHop(
          id: 7000 + i,
          chainId: 9001,
          position: i,
          proxy: chosen[i],
        ),
        growable: false,
      ),
    );
  }
}
