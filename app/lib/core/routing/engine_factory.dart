import 'dart:io' show Platform;

import '../net/host_pin.dart';
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
///
/// [hostPin] and [apiBaseUrl] are P26's mitigation. The Android engine
/// resolves the API host before it establishes the TUN and publishes the
/// address on [hostPin], which the HTTP transport then dials for the rest of
/// the session. Both are optional: a build that passes neither resolves
/// names the ordinary way, which is correct everywhere there is no tunnel.
ProxyEngine createDefaultEngine({HostPin? hostPin, String? apiBaseUrl}) {
  if (Platform.isAndroid) {
    return AndroidVpnEngine(hostPin: hostPin, apiBaseUrl: apiBaseUrl);
  }
  // The mock engine establishes nothing, so there is no window in which the
  // device's resolver stops working and nothing to pin against.
  return MockProxyEngine();
}
