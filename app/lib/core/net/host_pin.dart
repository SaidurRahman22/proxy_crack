import 'dart:io';

import 'package:flutter/foundation.dart';

/// Resolves a hostname to its addresses. Injectable so the pre-resolution
/// can be tested without a nameserver.
typedef HostResolver = Future<List<InternetAddress>> Function(String host);

/// A hostname could not be turned into an address.
///
/// Thrown only by the pre-resolution step, which runs **before** any tunnel
/// exists, so the message is always safe to show and always means "we did
/// not connect" rather than "we connected and then broke".
class HostResolutionException implements Exception {
  const HostResolutionException(this.host, this.message);

  final String host;

  /// One line, already user-facing.
  final String message;

  @override
  String toString() => 'HostResolutionException($host): $message';
}

/// One hostname pinned to one address for the lifetime of a tunnel session.
///
/// ## P26, and why this exists
///
/// The `process` routing rule in `XrayConfigBuilder` keeps **this app's own
/// sockets** out of the chain by matching our UID. It cannot keep our own
/// *name lookups* out of the chain, because on API 29+ an app's DNS does not
/// leave the app: `getaddrinfo` hands the query to `netd`, which runs as
/// **root (UID 0)** in a separate process. The packets that reach the tunnel
/// therefore carry netd's UID, and a rule written against ours can never
/// match them.
///
/// The consequence only appears in production, where the API base URL is a
/// hostname rather than the bare `10.0.2.2` a dev build points at:
///
/// 1. the chain breaks;
/// 2. the app tries to reach the API to re-resolve or re-fetch credentials;
/// 3. that lookup goes into the broken chain and never answers;
/// 4. the app cannot recover — the deadlock the pre-flight walk and the
///    unconditional off switch were built to remove, back in a weaker form.
///
/// ## Why netd's UID is *not* exempted
///
/// The obvious-looking fix is to add netd's UID to the `process` rule. It is
/// wrong, and badly so: netd resolves for **every app on the device**, so
/// exempting it takes the whole device's DNS off the tunnel. That is a
/// device-wide DNS leak — every hostname every app looks up goes to the
/// user's ISP in clear — and preventing exactly that is what this product
/// is for. The rule must stay keyed to our own UID; the fix has to be one
/// that does not need DNS at all.
///
/// ## What is done instead
///
/// Resolve the API host **before** `VpnService.establish()`, while the
/// device still has ordinary connectivity, and dial that address for the
/// rest of the session. There is then no name to look up while the tunnel is
/// up, so it does not matter whose UID a lookup would have carried.
///
/// The address is *pinned*, not merely cached: it is taken once per connect
/// and never re-taken mid-session. Re-resolving mid-session would reintroduce
/// the dependency this removes, at the exact moment it is least likely to
/// work. The next connect takes a fresh address.
///
/// ## What this class is **not**
///
/// It is not a general-purpose DNS cache and it must not become one. It
/// holds at most one host — ours — and it is consumed by exactly one place,
/// `createPinnedHttpClient`, which dials the pinned address while leaving
/// the request URL alone so TLS SNI, certificate validation and the `Host`
/// header all still carry the hostname.
class HostPin {
  String? _host;
  InternetAddress? _address;

  /// The pinned hostname, lower-cased, or null when nothing is pinned.
  String? get host => _host;

  /// The address [host] is pinned to, or null when nothing is pinned.
  InternetAddress? get address => _address;

  bool get isPinned => _host != null;

  /// Pins [host] to [address] for the session.
  void pin(String host, InternetAddress address) {
    _host = host.trim().toLowerCase();
    _address = address;
  }

  /// Drops the pin. Called when the tunnel is down and ordinary resolution
  /// works again.
  ///
  /// Deliberately **not** called when the engine reports an error: the
  /// service publishes an error while still holding the interface closed
  /// (fail closed), and that is precisely the state in which the app needs
  /// its pinned address to reach the backend.
  void clear() {
    _host = null;
    _address = null;
  }

  /// The address to dial for [candidate], or null when [candidate] is not
  /// the pinned host and should be resolved normally.
  InternetAddress? addressFor(String candidate) {
    final pinned = _host;
    if (pinned == null) return null;
    return candidate.trim().toLowerCase() == pinned ? _address : null;
  }

  @override
  String toString() =>
      _host == null ? 'HostPin(unpinned)' : 'HostPin($_host -> ${_address?.address})';
}

/// The hostname in [baseUrl] that needs pre-resolving, or null when there is
/// nothing to resolve.
///
/// Null means **no-op**, and there are two ways to earn it:
///
/// * the base URL is already an address — `http://10.0.2.2:8000`, today's
///   dev default, and `http://[::1]:8000`. Looking up an IP literal would
///   be a pointless round trip that can only fail;
/// * the base URL has no host at all, which is a configuration error the
///   transport reports far better than a resolver would.
String? hostToPin(String baseUrl) {
  final uri = Uri.tryParse(baseUrl.trim());
  if (uri == null) return null;
  final host = uri.host;
  if (host.isEmpty) return null;
  // `Uri.host` strips the brackets from `[::1]`, which is exactly the form
  // `InternetAddress.tryParse` wants.
  if (InternetAddress.tryParse(host) != null) return null;
  return host.toLowerCase();
}

/// Picks one address out of a lookup answer, deterministically.
///
/// ## The choice, and why it is this one
///
/// **IPv4 first, then the numerically lowest address.** A hostname with
/// several A records must produce the same pin on every connect from the
/// same answer, or a bug that depends on which address was taken becomes
/// unreproducible. Sorting the raw bytes gives a total order that does not
/// depend on the order the resolver happened to return.
///
/// IPv6 is accepted **only when there is no IPv4 address at all**, and that
/// fall-back is reported rather than taken silently. The app's whole DNS
/// path is `queryStrategy: UseIPv4` and the tunnel claims `::/0` so that
/// literal v6 destinations fail closed inside it rather than escaping
/// around it — so quietly pinning a v6 address would produce an API the
/// rest of the app's assumptions do not match. It is still better than
/// refusing outright: an IPv6-only network with DNS64/NAT64 (several
/// mobile carriers) returns nothing else, and the pinned API socket is
/// dialled by *our* process, which the `process` rule sends `direct` — so
/// it is carried by the physical interface and never by the chain.
///
/// Returns null for an empty answer.
@visibleForTesting
InternetAddress? selectPinnedAddress(List<InternetAddress> answers) {
  if (answers.isEmpty) return null;
  final v4 = answers
      .where((a) => a.type == InternetAddressType.IPv4)
      .toList(growable: false);
  final pool = v4.isNotEmpty ? v4 : answers;
  final sorted = List<InternetAddress>.of(pool)
    ..sort((a, b) => _compareBytes(a.rawAddress, b.rawAddress));
  return sorted.first;
}

int _compareBytes(List<int> a, List<int> b) {
  final shortest = a.length < b.length ? a.length : b.length;
  for (var i = 0; i < shortest; i++) {
    final difference = a[i] - b[i];
    if (difference != 0) return difference;
  }
  return a.length - b.length;
}

/// Resolves [host] to the one address the session will use.
///
/// Bounded by [timeout], because this runs on the connect path and an
/// unbounded lookup would be one more way for the connect button to hang.
///
/// Throws [HostResolutionException] when nothing usable comes back. The
/// caller is expected to refuse the connect: it is strictly better to stay
/// on the physical network with an error than to establish a tunnel the app
/// cannot reach its own backend through.
Future<InternetAddress> resolveHostOnce(
  String host, {
  HostResolver? resolver,
  Duration timeout = const Duration(seconds: 8),
}) async {
  final lookup = resolver ?? ((String name) => InternetAddress.lookup(name));
  List<InternetAddress> answers;
  try {
    answers = await lookup(host).timeout(timeout);
  } on Object {
    throw HostResolutionException(
      host,
      'Could not look up $host before connecting. The device has no working '
      'DNS right now, so the tunnel was not started.',
    );
  }
  final chosen = selectPinnedAddress(answers);
  if (chosen == null) {
    throw HostResolutionException(
      host,
      'The name $host does not resolve to any address from this network, so '
      'the tunnel was not started.',
    );
  }
  return chosen;
}
