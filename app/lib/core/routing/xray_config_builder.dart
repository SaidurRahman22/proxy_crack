import 'dart:convert';

import 'package:flutter/foundation.dart';

import '../../data/models/chain_hop.dart';
import '../../data/models/proxy.dart';
import '../../data/models/proxy_chain.dart';
import '../../data/models/proxy_credentials.dart';

/// A chain could not be turned into a usable engine config.
///
/// Thrown before anything native is touched, so the caller can surface a
/// precise message instead of a generic "connection failed".
class XrayConfigException implements Exception {
  const XrayConfigException(this.message);

  final String message;

  @override
  String toString() => 'XrayConfigException: $message';
}

/// Everything the privileged platform layer needs to stand a tunnel up.
///
/// engine-decision.md §4.2 rule 5: *"`start()` takes the complete config. L4
/// never constructs chain semantics."* The Kotlin service does not parse a
/// chain, does not know what a hop is, and never builds an `outbounds` array
/// — it substitutes the TUN file descriptor into [configJson] and copies the
/// numbers below into `VpnService.Builder`.
///
/// [mtu] appears here *and* inside [configJson] deliberately: §9.1 — the TUN
/// MTU and Xray's own MTU must match or you get a packet-size black hole that
/// looks like a flaky network. Carrying one value from one source removes the
/// chance of them drifting apart.
@immutable
class TunnelPlan {
  const TunnelPlan({
    required this.configJson,
    required this.validationConfigJson,
    required this.mtu,
    required this.ipv4Address,
    required this.ipv4Prefix,
    required this.ipv6Address,
    required this.ipv6Prefix,
    required this.routeIpv6,
    required this.dnsServers,
    required this.supportsUdp,
    required this.hopCount,
    required this.routeSummary,
    required this.chainName,
  });

  /// The complete Xray config, with [XrayConfigBuilder.tunFdPlaceholder]
  /// standing in for the runtime file descriptor.
  ///
  /// **Contains hop credentials. Never log it, never write it to disk.**
  final String configJson;

  /// The same config, prepared for libXray's `testXray`.
  ///
  /// `testXray` builds a real engine instance and closes it without starting
  /// it, which is how a generated config is checked against the real parser
  /// before any TUN exists. Two things are therefore removed:
  ///
  /// * the **`tun` inbound**, because building it means adopting a file
  ///   descriptor that `VpnService.establish()` has not produced yet — and
  ///   the entire point of validating here is that it happens *before* the
  ///   TUN is committed;
  /// * the **`xray.tun.fd` env entry**, whose value is still the runtime
  ///   placeholder.
  ///
  /// Everything a chain can get wrong — an outbound, a cipher, a
  /// `dialerProxy` tag that names nothing, a `qType` of the wrong JSON
  /// shape, a routing rule the parser rejects — lives in the parts that are
  /// kept. The TUN inbound that is dropped is a fixed constant, identical on
  /// every connect, already proven on a device.
  ///
  /// **Contains hop credentials.**
  final String validationConfigJson;

  final int mtu;

  final String ipv4Address;
  final int ipv4Prefix;

  final String ipv6Address;
  final int ipv6Prefix;

  /// Whether `::/0` is routed into the TUN.
  ///
  /// §8.6 item 4: a device with working IPv6 and only `0.0.0.0/0` routed
  /// sends every v6-capable destination straight out of the physical
  /// interface. That is a full traffic leak, not merely a DNS one. Claiming
  /// the route and failing closed inside the tunnel is the lesser evil.
  final bool routeIpv6;

  /// Addresses handed to `VpnService.Builder.addDnsServer`. Which address is
  /// used barely matters — the routing rules hijack port 53 regardless of
  /// destination (§8 layer 2) — but it must be inside the routed range so
  /// the packets enter the TUN at all.
  final List<String> dnsServers;

  /// False when any hop is HTTP (P7). The engine black-holes UDP in that
  /// case, so this is what the engine *actually does*, not an estimate.
  final bool supportsUdp;

  final int hopCount;

  /// `NL  ->  DE  ->  SG`, for the notification.
  final String routeSummary;

  final String chainName;

  /// The `MethodChannel.invokeMethod('start', ...)` payload.
  Map<String, Object?> toMethodArguments() => <String, Object?>{
        'config': configJson,
        'mtu': mtu,
        'ipv4Address': ipv4Address,
        'ipv4Prefix': ipv4Prefix,
        'ipv6Address': ipv6Address,
        'ipv6Prefix': ipv6Prefix,
        'routeIpv6': routeIpv6,
        'dnsServers': dnsServers,
        'supportsUdp': supportsUdp,
        'hopCount': hopCount,
        'routeSummary': routeSummary,
        'chainName': chainName,
      };

  /// Redacted on purpose — [configJson] holds passwords.
  @override
  String toString() =>
      'TunnelPlan($hopCount hops, mtu $mtu, udp $supportsUdp, config redacted)';
}

/// `ProxyChain` -> Xray JSON. Pure Dart, no platform, no I/O.
///
/// engine-decision.md §5.6: built in Dart rather than in each native layer
/// because it is the one piece of chain logic that must be identical on
/// Android, Windows, Linux and macOS — and because it is then testable with
/// no device, no emulator and no engine.
///
/// ## The three things that are easy to get silently wrong
///
/// 1. **Hop order reverses.** The user's *last* hop is the *first* outbound,
///    because an outbound dials *through* the outbound its `dialerProxy`
///    names. Emit them forwards and you get a chain running backwards, with
///    no error from the engine. [buildConfigMap] inverts; the unit tests pin
///    it.
/// 2. **`proxySettings` is dead.** Verified in `infra/conf/xray.go` at the
///    pinned commit: `OutboundDetourConfig.Build()` opens with
///    `if c.ProxySettings != nil { return nil, PrintRemovedFeatureError(...) }`.
///    The field survives only to produce that error. Chaining is
///    `streamSettings.sockopt.dialerProxy`.
/// 3. **`*+local://` and `localhost` DNS servers bypass routing** and dial
///    via `freedom`, i.e. straight out of the physical interface. That is a
///    DNS leak written into the config. [_validateDnsServers] refuses them.
class XrayConfigBuilder {
  const XrayConfigBuilder({
    this.mtu = defaultMtu,
    this.dnsServers = defaultDnsServers,
    this.tunnelDnsAddresses = defaultTunnelDnsAddresses,
    this.logLevel = defaultLogLevel,
    this.routeIpv6 = true,
  });

  /// Substituted by the platform layer with the real `ParcelFileDescriptor`
  /// number. Verified in Xray-core `common/platform/platform.go`:
  /// `TunFdKey = "xray.tun.fd"`, read through `EnvFlag.GetValue`, and the
  /// root `env` map is applied with `os.Setenv` in `Config.Build()`.
  static const String tunFdPlaceholder = '__RUNTIME_FD__';

  /// Root-`env` key the Android TUN reads the descriptor from.
  static const String tunFdEnvKey = 'xray.tun.fd';

  /// §9.1: start at 1500, drop to 1400 then 1280 if large transfers stall.
  /// 1280 is the IPv6 minimum and the safe floor.
  static const int defaultMtu = 1500;

  /// §8 layer 3. `tcp://` rather than a bare IP for two reasons: a chain
  /// with an HTTP hop cannot carry UDP at all, so a bare-IP server would
  /// try UDP and fail; and TCP DNS through a chain does not depend on every
  /// intermediate server implementing UDP ASSOCIATE.
  static const List<String> defaultDnsServers = <String>[
    'tcp://1.1.1.1:53',
    'tcp://8.8.8.8:53',
  ];

  /// What the OS resolver is pointed at.
  static const List<String> defaultTunnelDnsAddresses = <String>['1.1.1.1'];

  /// §7.1 note 10: on `debug`, Xray logs destination hostnames. That is a
  /// privacy incident in a VPN product, so the default never moves.
  static const String defaultLogLevel = 'warning';

  /// Carrier-grade NAT space is a poor choice (real traffic uses it), and
  /// 10.0.0.x collides with common home LANs. This block is rarely used.
  static const String tunIpv4Address = '10.215.173.1';
  static const int tunIpv4Prefix = 32;

  /// ULA, so it can never collide with a routable prefix.
  static const String tunIpv6Address = 'fdfe:dcba:9876::1';
  static const int tunIpv6Prefix = 126;

  /// Any non-empty name. **It must not be empty**, which is the one place
  /// engine-decision.md §4.3 and §7 are wrong about Android.
  ///
  /// Verified on-device, then in `infra/conf/tun.go`: when `name` is empty
  /// `TunConfig.Build()` calls `GetAvailableTunName()`, which calls
  /// `net.Interfaces()`, which opens a `netlink_route_socket`. SELinux
  /// denies that to `untrusted_app`, so the engine refuses the whole config
  /// with *"fail to get system interface information: route ip+net:
  /// netlinkrib: permission denied"* — after the TUN is already up, which
  /// makes it look like an engine fault rather than a config one.
  ///
  /// The value itself is cosmetic on Android: `tun_android.go`'s `NewTun`
  /// takes the descriptor from the environment and ignores `Name`. `tun0`
  /// is what `VpnService` actually creates, so it is at least truthful in
  /// a log line.
  static const String tunInterfaceName = 'tun0';

  static const String tunInboundTag = 'tun-in';
  static const String dnsInboundTag = 'dns-internal';
  static const String directOutboundTag = 'direct';
  static const String blockOutboundTag = 'block';
  static const String dnsOutboundTag = 'dns-out';

  final int mtu;
  final List<String> dnsServers;
  final List<String> tunnelDnsAddresses;
  final String logLevel;
  final bool routeIpv6;

  /// The outbound tag for a hop, e.g. `hop2-socks`.
  static String tagForHop(ChainHop hop) =>
      'hop${hop.position + 1}-${_protocolTag(hop.proxy.protocol)}';

  static String _protocolTag(ProxyProtocol protocol) => switch (protocol) {
        ProxyProtocol.shadowsocks => 'ss',
        ProxyProtocol.socks5 => 'socks',
        ProxyProtocol.http => 'http',
      };

  /// Xray's own name for the outbound protocol.
  static String _outboundProtocol(ProxyProtocol protocol) => switch (protocol) {
        ProxyProtocol.shadowsocks => 'shadowsocks',
        ProxyProtocol.socks5 => 'socks',
        ProxyProtocol.http => 'http',
      };

  TunnelPlan build(
    ProxyChain chain, {
    ChainCredentials? credentials,
    String? exemptProcessName,
  }) {
    final config = buildConfigMap(
      chain,
      credentials: credentials,
      exemptProcessName: exemptProcessName,
    );
    return TunnelPlan(
      configJson: jsonEncode(config),
      validationConfigJson: jsonEncode(validationConfigFrom(config)),
      mtu: mtu,
      ipv4Address: tunIpv4Address,
      ipv4Prefix: tunIpv4Prefix,
      ipv6Address: tunIpv6Address,
      ipv6Prefix: tunIpv6Prefix,
      routeIpv6: routeIpv6,
      dnsServers: List<String>.unmodifiable(tunnelDnsAddresses),
      supportsUdp: chain.supportsUdp,
      hopCount: chain.length,
      routeSummary: chain.routeSummary,
      chainName: chain.name,
    );
  }

  /// The config as a map, so tests can assert on structure rather than on
  /// the shape of a JSON string.
  Map<String, Object?> buildConfigMap(
    ProxyChain chain, {
    ChainCredentials? credentials,
    String? exemptProcessName,
  }) {
    if (chain.isEmpty) {
      throw const XrayConfigException(
        'A chain needs at least one hop before it can carry traffic.',
      );
    }
    _validateDnsServers(dnsServers);

    // The user reads hop 1 -> hop 2 -> hop 3. Xray reads the array the other
    // way: element 0 is the default outbound and therefore the hop that
    // *touches the destination*, which is the user's last hop.
    final ordered = List<ChainHop>.of(chain.hops)
      ..sort((a, b) => a.position.compareTo(b.position));

    final exitTag = tagForHop(ordered.last);

    final outbounds = <Map<String, Object?>>[
      // Descending: the user's last hop is emitted first and becomes the
      // default outbound.
      for (var i = ordered.length - 1; i >= 0; i--)
        _buildHopOutbound(
          ordered[i],
          // Each outbound dials through the hop *before* it in user order.
          // Hop 1 dials direct, so it gets no dialerProxy — emitting one
          // there would be a cycle, and a silent hang is worse than an
          // error.
          dialerProxy: i == 0 ? null : tagForHop(ordered[i - 1]),
          credentials: credentials?.forProxy(ordered[i].proxy.id),
        ),
      <String, Object?>{'tag': directOutboundTag, 'protocol': 'freedom'},
      <String, Object?>{'tag': blockOutboundTag, 'protocol': 'blackhole'},
      <String, Object?>{
        'tag': dnsOutboundTag,
        'protocol': 'dns',
        'settings': <String, Object?>{
          // Hijack A/AAAA into the DNS module; REFUSE everything else so an
          // app learns immediately rather than waiting out a timeout. This
          // also drops HTTPS/SVCB (type 65), which is what advertises QUIC.
          'rules': <Map<String, Object?>>[
            // `qType` is parsed as a PortList, and PortList.UnmarshalJSON
            // accepts a number or a comma/dash string — **not** a JSON
            // array. `[1, 28]` fails with "cannot unmarshal array into Go
            // value of type uint32", which is not an obvious thing to read
            // about a DNS query type.
            <String, Object?>{'action': 'hijack', 'qType': '1,28'},
            <String, Object?>{'action': 'return', 'rCode': 5},
          ],
        },
      },
    ];

    return <String, Object?>{
      'env': <String, Object?>{tunFdEnvKey: tunFdPlaceholder},
      'log': <String, Object?>{'loglevel': logLevel},
      'inbounds': <Map<String, Object?>>[
        <String, Object?>{
          'tag': tunInboundTag,
          'protocol': 'tun',
          // Verified in infra/conf/xray.go: InboundDetourConfig.Build()
          // skips port validation entirely when the protocol is "tun".
          'port': 0,
          'settings': <String, Object?>{
            // Never empty — see [tunInterfaceName].
            'name': tunInterfaceName,
            'mtu': mtu,
          },
        },
      ],
      'outbounds': outbounds,
      'dns': _buildDns(),
      'routing': _buildRouting(
        exitTag: exitTag,
        supportsUdp: chain.supportsUdp,
        exemptProcessName: exemptProcessName,
      ),
    };
  }

  /// Strips a config down to what libXray's `testXray` can build.
  ///
  /// See [TunnelPlan.validationConfigJson] for why the TUN inbound and the
  /// file-descriptor env entry come out. Operates on a copy — the config
  /// that is actually run is never touched.
  static Map<String, Object?> validationConfigFrom(
    Map<String, Object?> config,
  ) {
    final copy = Map<String, Object?>.of(config);

    final env = copy['env'];
    if (env is Map) {
      final trimmed = Map<String, Object?>.of(env.cast<String, Object?>())
        ..remove(tunFdEnvKey);
      if (trimmed.isEmpty) {
        copy.remove('env');
      } else {
        copy['env'] = trimmed;
      }
    }

    final inbounds = copy['inbounds'];
    if (inbounds is List) {
      copy['inbounds'] = <Object?>[
        for (final inbound in inbounds)
          if (!(inbound is Map && inbound['protocol'] == 'tun')) inbound,
      ];
    }

    return copy;
  }

  Map<String, Object?> _buildHopOutbound(
    ChainHop hop, {
    required String? dialerProxy,
    required ProxyCredentials? credentials,
  }) {
    final proxy = hop.proxy;
    if (proxy.host.isEmpty || proxy.port <= 0) {
      throw XrayConfigException(
        'Hop ${hop.position + 1} has no usable address.',
      );
    }

    return <String, Object?>{
      'tag': tagForHop(hop),
      'protocol': _outboundProtocol(proxy.protocol),
      'settings': _buildHopSettings(hop, credentials),
      'streamSettings': <String, Object?>{
        'sockopt': <String, Object?>{
          // §9.4: chain keepalives. The defaults assume a datacentre; three
          // hops means three idle timers that can each kill the whole chain.
          'tcpKeepAliveIdle': 30,
          'tcpKeepAliveInterval': 15,
          // §2.3: never emit happyEyeballs alongside dialerProxy — the Xray
          // docs say the combination stops happyEyeballs working.
          'dialerProxy': ?dialerProxy,
        },
      },
    };
  }

  Map<String, Object?> _buildHopSettings(
    ChainHop hop,
    ProxyCredentials? credentials,
  ) {
    final proxy = hop.proxy;
    final user = credentials?.username ?? '';
    final pass = credentials?.password ?? '';

    switch (proxy.protocol) {
      case ProxyProtocol.shadowsocks:
        // Shadowsocks has no anonymous mode: the password is the key.
        if (pass.isEmpty) {
          throw XrayConfigException(
            'Hop ${hop.position + 1} is Shadowsocks but no key was supplied. '
            'Shadowsocks cannot connect without one.',
          );
        }
        final method = credentials?.ssMethod ?? '';
        if (method.isEmpty) {
          throw XrayConfigException(
            'Hop ${hop.position + 1} is Shadowsocks but no cipher was '
            'supplied. Xray rejects an unknown cipher method.',
          );
        }
        return <String, Object?>{
          'address': proxy.host,
          'port': proxy.port,
          'method': method,
          'password': pass,
        };

      case ProxyProtocol.socks5:
      case ProxyProtocol.http:
        // Verified in infra/conf/socks.go and http.go: when `address` is
        // set, the flat form is expanded into servers[0], and a user entry
        // is only created when `user` is non-empty. So omitting both is a
        // no-auth proxy, not a broken one.
        return <String, Object?>{
          'address': proxy.host,
          'port': proxy.port,
          if (user.isNotEmpty) 'user': user,
          if (user.isNotEmpty) 'pass': pass,
        };
    }
  }

  Map<String, Object?> _buildDns() => <String, Object?>{
        'tag': dnsInboundTag,
        // Only A records. Combined with routing ::/0 into the TUN, this
        // keeps name-based traffic on v4 (which the chain carries) while
        // literal v6 destinations fail closed inside the tunnel instead of
        // escaping around it.
        'queryStrategy': 'UseIPv4',
        'disableFallback': false,
        'hosts': <String, Object?>{},
        'servers': List<String>.of(dnsServers),
      };

  Map<String, Object?> _buildRouting({
    required String exitTag,
    required bool supportsUdp,
    String? exemptProcessName,
  }) {
    return <String, Object?>{
      'domainStrategy': 'AsIs',
      'rules': <Map<String, Object?>>[
        // --- this app's own traffic, and nothing else ------------------
        //
        // Without this rule the app's calls to our own API enter the TUN
        // like everything else and are carried by the chain. When the chain
        // cannot carry traffic, the app can no longer reach the backend —
        // and the app is the only thing that can turn the tunnel off. That
        // is a deadlock, and it is how a kill switch becomes a bricked
        // device.
        //
        // Keyed on the **process**, not on the API host. A `direct` rule
        // naming our API would publish to the user's ISP that this device
        // runs this product — the single disclosure the product exists to
        // prevent — and would exempt *anyone's* traffic to that host, not
        // just ours. `process` matches one UID: ours.
        //
        // How it works (verified against the shipped binaries, not docs):
        // `LibXray.registerProcessFinder(finder, sdk)` installs a callback
        // that Xray calls with a connection's 4-tuple; libXray's
        // `controller_android.go` turns our `long` answer into the decimal
        // UID string, and `app/router.ProcessNameMatcher` compares that
        // string to the entries of this rule's `process` list with an exact
        // match. So the value here is our own UID, as decimal text.
        //
        // ⚠️ **Android only.** `registerProcessFinder` and `FindProcess`
        // are compiled under `//go:build android`; on Windows, Linux and
        // macOS this rule can never match and the platform needs its own
        // answer — most likely binding our own sockets outside the tunnel,
        // since every desktop target controls its own process and has no
        // equivalent of `VpnService.protect()`. Each desktop engine passes
        // null here until it has one, which emits no rule at all rather
        // than a rule that silently matches nothing.
        //
        // It is deliberately the **first** rule, ahead of the DNS hijack.
        // Exempting our API's TCP connection but still sending our own name
        // lookups into a chain that cannot carry them would leave exactly
        // the deadlock this rule exists to remove, for any API URL that is
        // a hostname rather than an address. The connection is already
        // going direct, and its TLS SNI already carries the hostname, so
        // resolving that same name outside the tunnel discloses nothing the
        // exemption had not already conceded.
        if (exemptProcessName != null && exemptProcessName.isNotEmpty)
          <String, Object?>{
            'type': 'field',
            'inboundTag': <String>[tunInboundTag],
            'process': <String>[exemptProcessName],
            'outboundTag': directOutboundTag,
          },
        // §8 layer 2, and the single most valuable rule in the file: catches
        // apps that hard-code 8.8.8.8 and ignore the system resolver. It is
        // first so that DNS survives the UDP black-hole rule below.
        <String, Object?>{
          'type': 'field',
          'inboundTag': <String>[tunInboundTag],
          'port': 53,
          'outboundTag': dnsOutboundTag,
        },
        // §8 layer 3, the layer people forget: the DNS module's own upstream
        // queries. Hijacking into the module achieves nothing if the module
        // then resolves from the physical interface.
        <String, Object?>{
          'type': 'field',
          'inboundTag': <String>[dnsInboundTag],
          'outboundTag': exitTag,
        },
        // P7. An HTTP hop is TCP-only, so a chain containing one cannot
        // carry UDP at all. Black-holing makes QUIC fail immediately and
        // fall back to TCP; letting it hang produces "the app is broken and
        // slow", which is a worse bug and a worse support ticket.
        if (!supportsUdp)
          <String, Object?>{
            'type': 'field',
            'inboundTag': <String>[tunInboundTag],
            'network': 'udp',
            'outboundTag': blockOutboundTag,
          },
        <String, Object?>{
          'type': 'field',
          'inboundTag': <String>[tunInboundTag],
          'outboundTag': exitTag,
        },
      ],
    };
  }

  /// §8 layer 4. Written once, protects every platform.
  static void _validateDnsServers(List<String> servers) {
    if (servers.isEmpty) {
      throw const XrayConfigException(
        'No DNS servers configured; the tunnel would have no resolver.',
      );
    }
    for (final server in servers) {
      final normalised = server.trim().toLowerCase();
      if (normalised.contains('+local://')) {
        throw XrayConfigException(
          'DNS server "$server" uses a +local:// scheme. Those bypass '
          'routing and dial via freedom — straight out of the physical '
          'interface. That is a DNS leak written into the config.',
        );
      }
      if (normalised == 'localhost' || normalised.startsWith('localhost:')) {
        throw XrayConfigException(
          'DNS server "$server" defers to the OS resolver, which Xray does '
          'not control and the tunnel cannot protect.',
        );
      }
    }
  }
}
