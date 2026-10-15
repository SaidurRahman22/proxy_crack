import 'dart:math';

import '../models/chain_hop.dart';
import '../models/plan.dart';
import '../models/proxy.dart';
import '../models/proxy_chain.dart';
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
        uploadBatchId: 40 + (i ~/ 60),
      );
    }, growable: false);
  }

  static const Plan freePlan = Plan(
    id: 1,
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
