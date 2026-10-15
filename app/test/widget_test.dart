import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proxy_crack/app/proxy_crack_app.dart';
import 'package:proxy_crack/core/routing/mock_proxy_engine.dart';
import 'package:proxy_crack/data/mock/mock_data.dart';
import 'package:proxy_crack/data/repositories/catalog_repository.dart';
import 'package:proxy_crack/data/models/proxy_chain.dart';

void main() {
  testWidgets('compact window gets bottom navigation at 375 px', (
    tester,
  ) async {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(375, 812);
    addTearDown(tester.view.reset);

    await tester.pumpWidget(
      ProxyCrackApp(
        engineOverride: MockProxyEngine(speedMultiplier: 50),
        repositoryOverride: MockCatalogRepository(latency: Duration.zero),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 16));

    expect(find.text('Proxy Crack'), findsOneWidget);
    expect(find.text('Not protected'), findsOneWidget);
    expect(find.byType(NavigationBar), findsOneWidget);
    expect(find.byType(NavigationRail), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('wide window swaps to a navigation rail', (tester) async {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(1280, 900);
    addTearDown(tester.view.reset);

    await tester.pumpWidget(
      ProxyCrackApp(
        engineOverride: MockProxyEngine(speedMultiplier: 50),
        repositoryOverride: MockCatalogRepository(latency: Duration.zero),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 16));

    expect(find.byType(NavigationRail), findsOneWidget);
    expect(find.byType(NavigationBar), findsNothing);
    expect(tester.takeException(), isNull);
  });

  test('chain latency compounds with every hop', () {
    final pool = MockData.proxies(count: 60);
    var chain = ProxyChain.empty();
    final healthy = pool.where((p) => p.isAlive).take(3).toList();

    chain = chain.withHopAdded(healthy[0]);
    final one = chain.estimatedLatencyMs;

    chain = chain.withHopAdded(healthy[1]);
    final two = chain.estimatedLatencyMs;

    chain = chain.withHopAdded(healthy[2]);

    expect(two, greaterThan(one));
    expect(chain.estimatedLatencyMs, greaterThan(two));
    expect(chain.latencyExpectation, LatencyExpectation.severe);
    expect(chain.hops.map((h) => h.position), <int>[0, 1, 2]);
  });

  test('reordering keeps positions contiguous', () {
    final pool = MockData.proxies(count: 30).where((p) => p.isAlive).toList();
    var chain = ProxyChain.empty()
        .withHopAdded(pool[0])
        .withHopAdded(pool[1])
        .withHopAdded(pool[2]);

    chain = chain.withHopMoved(2, 0);

    expect(chain.hops.first.proxy.id, pool[2].id);
    expect(chain.hops.map((h) => h.position), <int>[0, 1, 2]);
    expect(chain.hops.first.detourTag, isNull);
    expect(chain.hops[1].detourTag, 'hop1');
  });
}
