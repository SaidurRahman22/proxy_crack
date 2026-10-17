import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:proxy_crack/core/routing/xray_config_builder.dart';
import 'package:proxy_crack/data/models/chain_hop.dart';
import 'package:proxy_crack/data/models/country_code.dart';
import 'package:proxy_crack/data/models/proxy.dart';
import 'package:proxy_crack/data/models/proxy_chain.dart';
import 'package:proxy_crack/data/models/proxy_credentials.dart';

/// The config generator is the one piece of Phase 3 that can be tested to
/// destruction with no device, no emulator and no engine, so it is.
///
/// Four things here are worth more than the rest put together:
///
/// * **hop order reversal** — the user's last hop must be outbound zero;
/// * **`dialerProxy` wiring** — each outbound dials through the one before
///   it in user order, and hop 1 dials direct;
/// * **the DNS block** — three layers, and nothing that bypasses routing;
/// * **the UDP black-hole** — present when a chain contains an HTTP hop and
///   absent when it does not.
///
/// Each of those fails *silently* in production if it is wrong: a backwards
/// chain still connects, a leaking resolver still resolves, and a missing
/// UDP rule just makes QUIC hang for a few seconds.
void main() {
  Proxy proxy({
    required int id,
    required ProxyProtocol protocol,
    required String host,
    required int port,
    String country = 'Germany',
  }) {
    return Proxy(
      id: id,
      protocol: protocol,
      host: host,
      port: port,
      country: country,
      countryCode: CountryCode.fromCountry(country),
      city: 'Frankfurt',
      tier: ProxyTier.free,
      isAlive: true,
      latencyMs: 80,
      lastChecked: DateTime.fromMillisecondsSinceEpoch(0),
    );
  }

  ProxyChain chainOf(List<Proxy> proxies, {String name = 'Test chain'}) {
    return ProxyChain(
      id: 1,
      userId: 1,
      name: name,
      createdAt: DateTime.fromMillisecondsSinceEpoch(0),
      hops: <ChainHop>[
        for (var i = 0; i < proxies.length; i++)
          ChainHop(id: i + 1, chainId: 1, position: i, proxy: proxies[i]),
      ],
    );
  }

  ChainCredentials credsOf(Map<int, ProxyCredentials> map) =>
      ChainCredentials(map);

  List<Map<String, Object?>> outboundsOf(Map<String, Object?> config) =>
      (config['outbounds']! as List<Object?>).cast<Map<String, Object?>>();

  List<Map<String, Object?>> rulesOf(Map<String, Object?> config) {
    final routing = config['routing']! as Map<String, Object?>;
    return (routing['rules']! as List<Object?>).cast<Map<String, Object?>>();
  }

  String? dialerProxyOf(Map<String, Object?> outbound) {
    final stream = outbound['streamSettings'] as Map<String, Object?>?;
    final sockopt = stream?['sockopt'] as Map<String, Object?>?;
    return sockopt?['dialerProxy'] as String?;
  }

  final ssProxy = proxy(
    id: 10,
    protocol: ProxyProtocol.shadowsocks,
    host: '192.0.2.10',
    port: 8388,
    country: 'Netherlands',
  );
  final socksProxy = proxy(
    id: 20,
    protocol: ProxyProtocol.socks5,
    host: '198.51.100.20',
    port: 1080,
  );
  final httpProxy = proxy(
    id: 30,
    protocol: ProxyProtocol.http,
    host: '203.0.113.30',
    port: 8080,
    country: 'United States',
  );

  final threeHopCreds = credsOf(<int, ProxyCredentials>{
    10: const ProxyCredentials(
      proxyId: 10,
      username: '',
      password: 'ss-key',
      ssMethod: 'aes-256-gcm',
    ),
    20: const ProxyCredentials(proxyId: 20, username: 'u2', password: 'p2'),
    30: const ProxyCredentials(proxyId: 30, username: 'u3', password: 'p3'),
  });

  const builder = XrayConfigBuilder();

  group('hop order reversal — the thing that fails silently', () {
    test('the user\'s LAST hop is outbound zero', () {
      // SS -> SOCKS5 -> HTTP as the user reads it. The HTTP hop is the one
      // that touches the destination, so it must be the default outbound.
      final config = builder.buildConfigMap(
        chainOf(<Proxy>[ssProxy, socksProxy, httpProxy]),
        credentials: threeHopCreds,
      );

      final tags = outboundsOf(config)
          .map((o) => o['tag'] as String)
          .toList(growable: false);

      expect(
        tags,
        <String>[
          'hop3-http',
          'hop2-socks',
          'hop1-ss',
          'direct',
          'block',
          'dns-out',
        ],
        reason: 'emitted forwards, the chain runs backwards and nothing '
            'errors — the exit would be the hop the user put first',
      );
    });

    test('dialerProxy walks back towards hop 1, and hop 1 dials direct', () {
      final config = builder.buildConfigMap(
        chainOf(<Proxy>[ssProxy, socksProxy, httpProxy]),
        credentials: threeHopCreds,
      );
      final outbounds = outboundsOf(config);

      expect(dialerProxyOf(outbounds[0]), 'hop2-socks');
      expect(dialerProxyOf(outbounds[1]), 'hop1-ss');
      expect(
        dialerProxyOf(outbounds[2]),
        isNull,
        reason: 'a dialerProxy on hop 1 is a cycle; Xray may hang rather '
            'than complain',
      );
    });

    test('a hop order change changes the exit hop, not just the array', () {
      // Same three proxies, reversed. The exit must follow the user, not
      // the proxy list.
      final forward = builder.buildConfigMap(
        chainOf(<Proxy>[ssProxy, socksProxy, httpProxy]),
        credentials: threeHopCreds,
      );
      final backward = builder.buildConfigMap(
        chainOf(<Proxy>[httpProxy, socksProxy, ssProxy]),
        credentials: credsOf(<int, ProxyCredentials>{
          30: const ProxyCredentials(proxyId: 30, username: 'u3', password: 'p3'),
          20: const ProxyCredentials(proxyId: 20, username: 'u2', password: 'p2'),
          10: const ProxyCredentials(
            proxyId: 10,
            username: '',
            password: 'ss-key',
            ssMethod: 'aes-256-gcm',
          ),
        }),
      );

      expect(rulesOf(forward).last['outboundTag'], 'hop3-http');
      expect(rulesOf(backward).last['outboundTag'], 'hop3-ss');
    });

    test('a single hop has no dialerProxy at all', () {
      final config = builder.buildConfigMap(
        chainOf(<Proxy>[socksProxy]),
        credentials: credsOf(<int, ProxyCredentials>{
          20: const ProxyCredentials(proxyId: 20, username: 'u2', password: 'p2'),
        }),
      );
      final outbounds = outboundsOf(config);
      expect(outbounds.first['tag'], 'hop1-socks');
      expect(dialerProxyOf(outbounds.first), isNull);
    });

    test('never emits proxySettings, which only produces an error now', () {
      final json = jsonEncode(
        builder.buildConfigMap(
          chainOf(<Proxy>[ssProxy, socksProxy, httpProxy]),
          credentials: threeHopCreds,
        ),
      );
      expect(json.contains('proxySettings'), isFalse);
      // §2.3: the Xray docs say happyEyeballs stops working alongside
      // dialerProxy, so it must never appear on a chained outbound.
      expect(json.contains('happyEyeballs'), isFalse);
    });
  });

  group('per-protocol outbound settings', () {
    test('shadowsocks carries method and password in the flat form', () {
      final config = builder.buildConfigMap(
        chainOf(<Proxy>[ssProxy]),
        credentials: credsOf(<int, ProxyCredentials>{
          10: const ProxyCredentials(
            proxyId: 10,
            username: '',
            password: 'ss-key',
            ssMethod: '2022-blake3-aes-256-gcm',
          ),
        }),
      );
      final outbound = outboundsOf(config).first;

      expect(outbound['protocol'], 'shadowsocks');
      expect(outbound['settings'], <String, Object?>{
        'address': '192.0.2.10',
        'port': 8388,
        'method': '2022-blake3-aes-256-gcm',
        'password': 'ss-key',
      });
    });

    test('socks uses user/pass, not username/password', () {
      final config = builder.buildConfigMap(
        chainOf(<Proxy>[socksProxy]),
        credentials: credsOf(<int, ProxyCredentials>{
          20: const ProxyCredentials(proxyId: 20, username: 'u2', password: 'p2'),
        }),
      );
      final outbound = outboundsOf(config).first;

      expect(outbound['protocol'], 'socks');
      expect(outbound['settings'], <String, Object?>{
        'address': '198.51.100.20',
        'port': 1080,
        'user': 'u2',
        'pass': 'p2',
      });
    });

    test('http uses the same flat shape', () {
      final config = builder.buildConfigMap(
        chainOf(<Proxy>[httpProxy]),
        credentials: credsOf(<int, ProxyCredentials>{
          30: const ProxyCredentials(proxyId: 30, username: 'u3', password: 'p3'),
        }),
      );
      final outbound = outboundsOf(config).first;

      expect(outbound['protocol'], 'http');
      expect(outbound['settings'], <String, Object?>{
        'address': '203.0.113.30',
        'port': 8080,
        'user': 'u3',
        'pass': 'p3',
      });
    });

    test('a credential-free socks hop emits no user key', () {
      // Verified in infra/conf/socks.go: a user entry is only created when
      // `user` is non-empty, so omitting it is a no-auth proxy rather than
      // a broken one. Emitting an empty username would make Xray offer
      // username/password auth with an empty username.
      final config = builder.buildConfigMap(chainOf(<Proxy>[socksProxy]));
      final settings = outboundsOf(config).first['settings']!
          as Map<String, Object?>;
      expect(settings.containsKey('user'), isFalse);
      expect(settings.containsKey('pass'), isFalse);
    });

    test('shadowsocks without a key is refused before anything native runs',
        () {
      expect(
        () => builder.buildConfigMap(chainOf(<Proxy>[ssProxy])),
        throwsA(
          isA<XrayConfigException>().having(
            (e) => e.message,
            'message',
            contains('Shadowsocks'),
          ),
        ),
      );
    });

    test('shadowsocks without a cipher is refused', () {
      expect(
        () => builder.buildConfigMap(
          chainOf(<Proxy>[ssProxy]),
          credentials: credsOf(<int, ProxyCredentials>{
            10: const ProxyCredentials(
              proxyId: 10,
              username: '',
              password: 'ss-key',
            ),
          }),
        ),
        throwsA(
          isA<XrayConfigException>().having(
            (e) => e.message,
            'message',
            contains('cipher'),
          ),
        ),
      );
    });

    test('an empty chain is refused', () {
      expect(
        () => builder.buildConfigMap(ProxyChain.empty()),
        throwsA(isA<XrayConfigException>()),
      );
    });
  });

  group('DNS — a leak here defeats the product', () {
    final config = builder.buildConfigMap(
      chainOf(<Proxy>[ssProxy, socksProxy, httpProxy]),
      credentials: threeHopCreds,
    );

    test('layer 2: port 53 from the TUN is hijacked into the dns outbound',
        () {
      final rule = rulesOf(config).first;
      expect(rule['inboundTag'], <String>['tun-in']);
      expect(rule['port'], 53);
      expect(rule['outboundTag'], 'dns-out');
    });

    test('layer 2 comes before the UDP black-hole, or DNS dies with it', () {
      final rules = rulesOf(config);
      final dnsIndex = rules.indexWhere((r) => r['outboundTag'] == 'dns-out');
      final blockIndex = rules.indexWhere((r) => r['outboundTag'] == 'block');
      expect(dnsIndex, lessThan(blockIndex));
    });

    test('layer 3: the resolver\'s own queries go through the exit hop', () {
      final rule = rulesOf(config).firstWhere(
        (r) => (r['inboundTag'] as List<Object?>).contains('dns-internal'),
      );
      expect(
        rule['outboundTag'],
        'hop3-http',
        reason: 'hijacking queries into the DNS module achieves nothing if '
            'the module then resolves from the physical interface',
      );
    });

    test('upstream servers are tcp://, never bare IPs', () {
      final dns = config['dns']! as Map<String, Object?>;
      final servers = (dns['servers']! as List<Object?>).cast<String>();
      expect(servers, isNotEmpty);
      for (final server in servers) {
        expect(
          server.startsWith('tcp://') || server.startsWith('https://'),
          isTrue,
          reason: 'a bare IP means UDP, and a chain with an HTTP hop cannot '
              'carry UDP at all',
        );
      }
      expect((dns['tag'] as String?), 'dns-internal');
    });

    test('layer 4: +local:// is refused', () {
      const leaky = XrayConfigBuilder(
        dnsServers: <String>['tcp+local://1.1.1.1:53'],
      );
      expect(
        () => leaky.buildConfigMap(
          chainOf(<Proxy>[socksProxy]),
          credentials: credsOf(<int, ProxyCredentials>{
            20: const ProxyCredentials(
              proxyId: 20,
              username: 'u2',
              password: 'p2',
            ),
          }),
        ),
        throwsA(
          isA<XrayConfigException>().having(
            (e) => e.message,
            'message',
            contains('bypass'),
          ),
        ),
      );
    });

    test('layer 4: localhost is refused', () {
      const leaky = XrayConfigBuilder(dnsServers: <String>['localhost']);
      expect(
        () => leaky.buildConfigMap(
          chainOf(<Proxy>[socksProxy]),
          credentials: credsOf(<int, ProxyCredentials>{
            20: const ProxyCredentials(
              proxyId: 20,
              username: 'u2',
              password: 'p2',
            ),
          }),
        ),
        throwsA(isA<XrayConfigException>()),
      );
    });

    test('the dns outbound hijacks A/AAAA and refuses the rest', () {
      final dnsOut = outboundsOf(config).firstWhere(
        (o) => o['tag'] == 'dns-out',
      );
      final settings = dnsOut['settings']! as Map<String, Object?>;
      final rules = (settings['rules']! as List<Object?>)
          .cast<Map<String, Object?>>();
      expect(rules.first['action'], 'hijack');
      // A string, not an array: qType parses as a PortList, which rejects
      // a JSON array outright.
      expect(rules.first['qType'], '1,28');
      expect(rules.last['action'], 'return');
    });
  });

  group('UDP honesty (P7)', () {
    test('a chain with an HTTP hop black-holes UDP', () {
      final chain = chainOf(<Proxy>[ssProxy, httpProxy]);
      expect(chain.supportsUdp, isFalse);

      final config = builder.buildConfigMap(
        chain,
        credentials: credsOf(<int, ProxyCredentials>{
          10: const ProxyCredentials(
            proxyId: 10,
            username: '',
            password: 'ss-key',
            ssMethod: 'aes-256-gcm',
          ),
          30: const ProxyCredentials(
            proxyId: 30,
            username: 'u3',
            password: 'p3',
          ),
        }),
      );

      final udpRule = rulesOf(config).firstWhere(
        (r) => r['network'] == 'udp',
        orElse: () => <String, Object?>{},
      );
      expect(
        udpRule['outboundTag'],
        'block',
        reason: 'without this, QUIC hangs for seconds before falling back '
            'and the user reads it as "the app is broken and slow"',
      );
    });

    test('an all-SS/SOCKS chain emits no UDP rule, so UDP flows', () {
      final chain = chainOf(<Proxy>[ssProxy, socksProxy]);
      expect(chain.supportsUdp, isTrue);

      final config = builder.buildConfigMap(
        chain,
        credentials: credsOf(<int, ProxyCredentials>{
          10: const ProxyCredentials(
            proxyId: 10,
            username: '',
            password: 'ss-key',
            ssMethod: 'aes-256-gcm',
          ),
          20: const ProxyCredentials(
            proxyId: 20,
            username: 'u2',
            password: 'p2',
          ),
        }),
      );

      expect(
        rulesOf(config).any((r) => r['network'] == 'udp'),
        isFalse,
      );
    });

    test('supportsUdp is weakest-hop, and an empty chain carries nothing',
        () {
      expect(chainOf(<Proxy>[socksProxy, ssProxy]).supportsUdp, isTrue);
      expect(chainOf(<Proxy>[socksProxy, httpProxy]).supportsUdp, isFalse);
      expect(chainOf(<Proxy>[httpProxy]).supportsUdp, isFalse);
      expect(ProxyChain.empty().supportsUdp, isFalse);
      expect(
        chainOf(<Proxy>[ssProxy, httpProxy, socksProxy]).tcpOnlyHops.single
            .proxy
            .id,
        30,
      );
    });

    test('the plan reports what the engine will actually do', () {
      final plan = builder.build(
        chainOf(<Proxy>[socksProxy, httpProxy]),
        credentials: credsOf(<int, ProxyCredentials>{
          20: const ProxyCredentials(
            proxyId: 20,
            username: 'u2',
            password: 'p2',
          ),
          30: const ProxyCredentials(
            proxyId: 30,
            username: 'u3',
            password: 'p3',
          ),
        }),
      );
      expect(plan.supportsUdp, isFalse);
      expect(plan.hopCount, 2);
    });
  });

  group('TUN inbound and fd handoff', () {
    final config = builder.buildConfigMap(
      chainOf(<Proxy>[socksProxy]),
      credentials: credsOf(<int, ProxyCredentials>{
        20: const ProxyCredentials(proxyId: 20, username: 'u2', password: 'p2'),
      }),
    );

    test('the descriptor arrives through root env, not a settings field', () {
      final env = config['env']! as Map<String, Object?>;
      expect(env['xray.tun.fd'], XrayConfigBuilder.tunFdPlaceholder);
    });

    test('one tun inbound, with the MTU the TUN will be built with', () {
      final inbounds =
          (config['inbounds']! as List<Object?>).cast<Map<String, Object?>>();
      expect(inbounds, hasLength(1));
      expect(inbounds.single['protocol'], 'tun');
      expect(inbounds.single['tag'], 'tun-in');

      final settings = inbounds.single['settings']! as Map<String, Object?>;
      // §9.1: a mismatch between this and VpnService.Builder.setMtu() is a
      // packet-size black hole that looks like a flaky network.
      expect(settings['mtu'], XrayConfigBuilder.defaultMtu);
    });

    test('the interface name is never empty', () {
      // Regression. An empty name sends TunConfig.Build() into
      // GetAvailableTunName() -> net.Interfaces() -> a netlink route
      // socket, which SELinux denies to untrusted_app. The engine then
      // rejects the whole config — after the TUN is already established,
      // so it reads as an engine fault rather than a config one.
      final inbounds =
          (config['inbounds']! as List<Object?>).cast<Map<String, Object?>>();
      final settings = inbounds.single['settings']! as Map<String, Object?>;
      expect(settings['name'], isNotEmpty);
      expect(settings['name'], XrayConfigBuilder.tunInterfaceName);
    });

    test('logs stay at warning, because debug logs destination hostnames',
        () {
      final log = config['log']! as Map<String, Object?>;
      expect(log['loglevel'], 'warning');
    });

    test('the plan hands the platform layer a matching MTU', () {
      final plan = builder.build(
        chainOf(<Proxy>[socksProxy]),
        credentials: credsOf(<int, ProxyCredentials>{
          20: const ProxyCredentials(
            proxyId: 20,
            username: 'u2',
            password: 'p2',
          ),
        }),
      );
      final decoded = jsonDecode(plan.configJson) as Map<String, dynamic>;
      final inbound = (decoded['inbounds'] as List<dynamic>).first
          as Map<String, dynamic>;
      final settings = inbound['settings'] as Map<String, dynamic>;
      expect(plan.mtu, settings['mtu']);
    });

    test('toString never leaks the config', () {
      final plan = builder.build(
        chainOf(<Proxy>[socksProxy]),
        credentials: credsOf(<int, ProxyCredentials>{
          20: const ProxyCredentials(
            proxyId: 20,
            username: 'secret-user',
            password: 'secret-pass',
          ),
        }),
      );
      expect(plan.toString().contains('secret-pass'), isFalse);
      expect(plan.toString().contains('secret-user'), isFalse);
    });
  });
}
