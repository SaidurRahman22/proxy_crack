import 'package:flutter/foundation.dart';

/// One hop's paid credentials, fetched from its `credentials_url`.
///
/// ## Handling rules — these are the product, not a detail
///
/// * **Fetched at connect time only.** Never on list render, never on screen
///   focus, never on a tab switch. `GET /api/proxies/{id}/` carries the
///   `proxy_credentials` throttle at **60/hour** and writes an audit row per
///   call (D19), so a three-hop connect spends 3 of 60 and a screen that
///   fetched on build would exhaust the budget in under a minute.
/// * **Never cached and never persisted.** Not to secure storage, not to a
///   field on a controller, not into a provider cache. It lives in a local
///   variable from the fetch until the engine has it, then goes out of
///   scope.
/// * **Never logged.** [toString] redacts, and the class carries no
///   `toJson`, deliberately: there is nothing legitimate to serialise it
///   into.
@immutable
class ProxyCredentials {
  const ProxyCredentials({
    required this.proxyId,
    required this.username,
    required this.password,
    this.ssMethod = '',
  });

  final int proxyId;
  final String username;
  final String password;

  /// Shadowsocks cipher, e.g. `aes-256-gcm`. Empty for SOCKS5/HTTP.
  final String ssMethod;

  bool get isEmpty => username.isEmpty && password.isEmpty;

  factory ProxyCredentials.fromJson(Map<String, dynamic> json) =>
      ProxyCredentials(
        proxyId: (json['id'] as num?)?.toInt() ?? 0,
        username: json['username'] as String? ?? '',
        password: json['password'] as String? ?? '',
        ssMethod: json['ss_method'] as String? ?? '',
      );

  @override
  String toString() =>
      'ProxyCredentials(proxy: $proxyId, username: <redacted>, '
      'password: <redacted>)';
}

/// Credentials for a whole resolved chain, keyed by proxy id.
///
/// Handed straight to `ProxyEngine.connect` and dropped. Holding it anywhere
/// that outlives a connect attempt breaks the rule above.
@immutable
class ChainCredentials {
  const ChainCredentials(this._byProxyId);

  final Map<int, ProxyCredentials> _byProxyId;

  static const ChainCredentials empty = ChainCredentials(
    <int, ProxyCredentials>{},
  );

  ProxyCredentials? forProxy(int proxyId) => _byProxyId[proxyId];

  int get length => _byProxyId.length;

  @override
  String toString() => 'ChainCredentials(${_byProxyId.length} hops, redacted)';
}
