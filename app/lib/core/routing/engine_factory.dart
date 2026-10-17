import 'dart:io' show Platform;

import 'mock_proxy_engine.dart';
import 'platform/android_vpn_engine.dart';
import 'proxy_engine.dart';

/// Picks the [ProxyEngine] for the host this build is running on.
///
/// This file and `platform/` are the only places in the app allowed to ask
/// what OS they are on (engine-decision.md §4.2 rule 1: *"the UI must never
/// branch on `Platform.isWindows`"*). Everywhere above `core/routing/` the
/// answer is simply "a `ProxyEngine`".
///
/// Everything that is not Android gets [MockProxyEngine]:
///
/// * **Tests** run on the host VM, so a widget test needs no platform
///   channel, no emulator and no engine.
/// * **Desktop** has no tunnel here yet (phases 7-9). A mock that visibly
///   simulates is better than a real engine that silently routes nothing,
///   and `supportsSystemWideRouting` lets the UI degrade honestly once the
///   desktop engines arrive.
ProxyEngine createDefaultEngine() {
  if (Platform.isAndroid) return AndroidVpnEngine();
  return MockProxyEngine();
}
