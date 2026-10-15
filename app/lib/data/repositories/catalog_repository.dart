import 'dart:async';

import '../mock/mock_data.dart';
import '../models/plan.dart';
import '../models/proxy.dart';
import '../models/subscription.dart';

/// Read side of the app's data.
///
/// The production implementation will be a DRF client hitting the endpoints
/// in architecture.md section 3. The interface is defined here so the screens
/// never learn which one they got.
abstract class CatalogRepository {
  /// `GET /api/proxies/` -- already entitlement-scoped by the server.
  Future<List<Proxy>> fetchProxies();

  /// Plan catalogue for the paywall.
  Future<List<Plan>> fetchPlans();

  /// `GET /api/subscription/` -- current plan plus entitlements.
  Future<Subscription> fetchSubscription();
}

/// Mock implementation with realistic delays so loading states get exercised
/// during development rather than only in production.
class MockCatalogRepository implements CatalogRepository {
  MockCatalogRepository({this.latency = const Duration(milliseconds: 900)});

  final Duration latency;

  List<Proxy>? _cachedProxies;

  @override
  Future<List<Proxy>> fetchProxies() async {
    await Future<void>.delayed(latency);
    return _cachedProxies ??= MockData.proxies();
  }

  @override
  Future<List<Plan>> fetchPlans() async {
    await Future<void>.delayed(latency * 0.7);
    return MockData.plans;
  }

  @override
  Future<Subscription> fetchSubscription() async {
    await Future<void>.delayed(latency * 0.5);
    return MockData.currentSubscription;
  }
}
