import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';

import '../../data/models/chain_hop.dart';
import '../../data/models/proxy.dart';
import '../../data/models/proxy_chain.dart';
import '../../data/models/proxy_credentials.dart';

/// What the pre-flight walk concluded about a chain.
enum PreflightOutcome {
  /// Every hop we could probe answered, and each one let us through to the
  /// next. The chain looks viable.
  reachable,

  /// A hop refused the connection to the *next* hop with a policy refusal
  /// (HTTP 403, or SOCKS5 reply 0x02 "not allowed by ruleset").
  ///
  /// This is P25. Commercial providers block CONNECT back into their own
  /// IP ranges as ordinary anti-abuse, so two consecutive hops bought from
  /// the same provider can never chain, no matter how healthy both are.
  /// Reported separately from [hopUnreachable] because the remedy is
  /// completely different: not "retry" or "replace the hop", but "use a
  /// different provider for this position".
  providerBlocked,

  /// A hop did not answer at all, or refused outright.
  hopUnreachable,

  /// A hop answered and rejected the credentials.
  authenticationFailed,

  /// Nothing conclusive could be learned, and that is not a reason to
  /// refuse the connect. Covers a Shadowsocks hop (which by design answers
  /// nothing a probe can read — see P14) and a chain short enough to have
  /// no hop-to-hop link to test.
  inconclusive,
}

/// The result of probing a chain before any tunnel exists.
@immutable
class PreflightResult {
  const PreflightResult({
    required this.outcome,
    required this.detail,
    required this.hopsProbed,
    this.failingHopNumber,
  });

  final PreflightOutcome outcome;

  /// One line, already user-facing. Never a socket error verbatim.
  final String detail;

  /// How many hops the walk actually reached, 1-based. `2` means hop 2 was
  /// reached through hop 1.
  final int hopsProbed;

  /// The 1-based hop that could not be reached, when there is one.
  final int? failingHopNumber;

  /// Whether the connect should go ahead.
  ///
  /// [PreflightOutcome.inconclusive] passes deliberately: refusing a chain
  /// because we could not measure it would make a Shadowsocks first hop
  /// unusable, and "we could not check" is not evidence of a fault.
  bool get isViable =>
      outcome == PreflightOutcome.reachable ||
      outcome == PreflightOutcome.inconclusive;

  @override
  String toString() =>
      'PreflightResult(${outcome.name}, probed $hopsProbed, $detail)';
}

/// Walks a chain hop by hop over real sockets, before a TUN exists.
///
/// ## Why this exists
///
/// Without it the order of events is: establish the TUN, move the default
/// route onto it, start the engine, and *then* discover that hop 1 will not
/// connect to hop 2. By that point the device has no network and the only
/// thing holding it is the app whose own API calls now go through the dead
/// chain. That is how a kill switch becomes a brick.
///
/// So this runs first, on the physical network, and the TUN is only
/// established once the chain has demonstrated it can carry a connection.
///
/// ## Why it is in Dart rather than in the platform layer
///
/// It is the same check on every platform (D8), it needs the chain and the
/// credentials — both of which live here — and written this way it is
/// testable against a real socket server in a unit test, with no emulator
/// and no engine.
///
/// ## What it actually does
///
/// Exactly what the engine will do: open a TCP connection to hop 1, then
/// ask hop 1 to CONNECT to hop 2, then ask hop 2 (through hop 1) to CONNECT
/// to hop 3, and so on. That is `dialerProxy` chaining performed by hand, so
/// a refusal here is a refusal the engine would also have hit — not a
/// different test that happens to correlate.
///
/// It stops at the last hop. It deliberately does **not** then reach out to
/// a neutral third-party host to prove egress: that would hand a third party
/// a connection from every chain the user builds, which is precisely the
/// disclosure this product exists to avoid (cf. D22).
class ChainPreflight {
  ChainPreflight({
    this.connectTimeout = const Duration(seconds: 8),
    this.handshakeTimeout = const Duration(seconds: 8),
    this.totalTimeout = const Duration(seconds: 20),
  });

  /// Per-socket connect ceiling.
  final Duration connectTimeout;

  /// Per-hop negotiation ceiling.
  final Duration handshakeTimeout;

  /// Ceiling for the whole walk, however many hops it has.
  final Duration totalTimeout;

  Socket? _socket;
  bool _cancelled = false;

  /// Abandons an in-flight walk.
  ///
  /// Called when the user asks to disconnect mid-connect. Destroying the
  /// socket makes every pending read complete immediately, so the connect
  /// sequence unwinds at once instead of sitting out [totalTimeout] — the
  /// off switch must never wait on the network.
  void cancel() {
    _cancelled = true;
    try {
      _socket?.destroy();
    } on Object {
      // Already gone.
    }
    _socket = null;
  }

  /// Walks [chain], dialling hop 1 at [firstHopAddress] when one is given.
  ///
  /// ## Why hop 1 gets an address and the rest do not
  ///
  /// P26 again, from the other side. This walk runs before the TUN exists,
  /// so in the ordinary case its own name lookups are carried by the
  /// physical network and nothing is wrong. The case that is not ordinary is
  /// a *later* connect attempt: the service publishes an error while still
  /// holding the interface closed when the engine dies under it (fail
  /// closed), and in that state nothing here is connected but the TUN is
  /// still up. A hostname hop would then be looked up by `netd`, as root,
  /// into a chain that carries nothing — and the pre-flight that exists to
  /// stop a bad chain being committed would itself hang on one.
  ///
  /// So the caller pre-resolves hop 1 in the same bounded step that pins the
  /// API host, and passes the address here. `Socket.connect` with an
  /// [InternetAddress] performs no lookup at all.
  ///
  /// Hops 2..n need nothing: they are named to the *previous hop* — in the
  /// `CONNECT` request line, or as a SOCKS5 `ATYP 0x03` domain — and
  /// resolved by that proxy server, never by this device. Addressing them by
  /// name is also deliberate (the engine does the same), so the walk tests
  /// what the engine will actually do.
  Future<PreflightResult> run(
    ProxyChain chain, {
    ChainCredentials? credentials,
    InternetAddress? firstHopAddress,
  }) async {
    try {
      return await _run(chain, credentials, firstHopAddress)
          .timeout(totalTimeout, onTimeout: _timedOut);
    } finally {
      cancel();
    }
  }

  PreflightResult _timedOut() => const PreflightResult(
    outcome: PreflightOutcome.hopUnreachable,
    detail:
        'The chain did not answer in time. At least one hop is not '
        'responding.',
    hopsProbed: 0,
  );

  Future<PreflightResult> _run(
    ProxyChain chain,
    ChainCredentials? credentials,
    InternetAddress? firstHopAddress,
  ) async {
    final hops = List<ChainHop>.of(chain.hops)
      ..sort((a, b) => a.position.compareTo(b.position));
    if (hops.isEmpty) {
      return const PreflightResult(
        outcome: PreflightOutcome.inconclusive,
        detail: 'This chain has no hops.',
        hopsProbed: 0,
      );
    }

    final first = hops.first.proxy;
    final Socket socket;
    try {
      socket = await Socket.connect(
        // An InternetAddress here means no lookup; a String means the
        // ordinary one. Both dial the same hop.
        firstHopAddress ?? first.host,
        first.port,
        timeout: connectTimeout,
      );
    } on Object {
      return const PreflightResult(
        outcome: PreflightOutcome.hopUnreachable,
        detail:
            'Hop 1 did not answer. It is down or unreachable from this '
            'network — replace it and try again.',
        hopsProbed: 0,
        failingHopNumber: 1,
      );
    }
    if (_cancelled) {
      socket.destroy();
      return const PreflightResult(
        outcome: PreflightOutcome.inconclusive,
        detail: 'Cancelled.',
        hopsProbed: 1,
      );
    }
    _socket = socket;
    final reader = _SocketReader(socket);

    try {
      if (hops.length == 1) {
        // Nothing to chain through. Hop 1 answering a TCP connection is all
        // there is to learn without involving a third party.
        return const PreflightResult(
          outcome: PreflightOutcome.reachable,
          detail: 'Hop 1 answered.',
          hopsProbed: 1,
        );
      }

      var probed = 1;
      for (var i = 1; i < hops.length; i++) {
        final via = hops[i - 1].proxy;
        final target = hops[i].proxy;
        final hopNumber = i + 1;

        if (via.protocol == ProxyProtocol.shadowsocks) {
          // A Shadowsocks server answers nothing it cannot decrypt, and
          // answers a correct key with silence too (P14). Speaking it here
          // would mean a second AEAD client in the app for a check that can
          // only ever say "maybe". Say so instead of guessing.
          return PreflightResult(
            outcome: PreflightOutcome.inconclusive,
            detail:
                'Checked as far as hop $probed. Hop $probed is Shadowsocks, '
                'which cannot be probed without carrying traffic.',
            hopsProbed: probed,
          );
        }

        final result = await _reachThrough(
          socket: socket,
          reader: reader,
          via: via,
          viaNumber: i,
          targetHost: target.host,
          targetPort: target.port,
          targetNumber: hopNumber,
          credentials: credentials?.forProxy(via.id),
        ).timeout(handshakeTimeout, onTimeout: _timedOut);

        if (!result.isViable) return result;
        probed = hopNumber;
      }

      return PreflightResult(
        outcome: PreflightOutcome.reachable,
        detail: 'All $probed hops answered, each through the one before it.',
        hopsProbed: probed,
      );
    } on Object {
      return const PreflightResult(
        outcome: PreflightOutcome.hopUnreachable,
        detail:
            'The chain dropped the connection while it was being checked.',
        hopsProbed: 0,
      );
    } finally {
      await reader.dispose();
    }
  }

  /// Asks [via] to open a connection to `targetHost:targetPort` over the
  /// already-established [socket], leaving the socket tunnelled to the
  /// target on success so the next hop can be asked in turn.
  Future<PreflightResult> _reachThrough({
    required Socket socket,
    required _SocketReader reader,
    required Proxy via,
    required int viaNumber,
    required String targetHost,
    required int targetPort,
    required int targetNumber,
    required ProxyCredentials? credentials,
  }) async {
    switch (via.protocol) {
      case ProxyProtocol.http:
        return _httpConnect(
          socket: socket,
          reader: reader,
          viaNumber: viaNumber,
          targetHost: targetHost,
          targetPort: targetPort,
          targetNumber: targetNumber,
          credentials: credentials,
        );
      case ProxyProtocol.socks5:
        return _socksConnect(
          socket: socket,
          reader: reader,
          viaNumber: viaNumber,
          targetHost: targetHost,
          targetPort: targetPort,
          targetNumber: targetNumber,
          credentials: credentials,
        );
      case ProxyProtocol.shadowsocks:
        return PreflightResult(
          outcome: PreflightOutcome.inconclusive,
          detail: 'Hop $viaNumber is Shadowsocks and cannot be probed.',
          hopsProbed: viaNumber,
        );
    }
  }

  // --- HTTP CONNECT ------------------------------------------------------

  Future<PreflightResult> _httpConnect({
    required Socket socket,
    required _SocketReader reader,
    required int viaNumber,
    required String targetHost,
    required int targetPort,
    required int targetNumber,
    required ProxyCredentials? credentials,
  }) async {
    final authority = '$targetHost:$targetPort';
    final request = StringBuffer()
      ..write('CONNECT $authority HTTP/1.1\r\n')
      ..write('Host: $authority\r\n');
    if (credentials != null && credentials.username.isNotEmpty) {
      final token = base64Encode(
        '${credentials.username}:${credentials.password}',
      );
      request.write('Proxy-Authorization: Basic $token\r\n');
    }
    request.write('\r\n');
    // The request line names the next hop's address, never a credential;
    // the header is base64 and is never logged.
    socket.add(_ascii(request.toString()));
    await socket.flush();

    final statusLine = await reader.readLine();
    if (statusLine == null) {
      return PreflightResult(
        outcome: PreflightOutcome.hopUnreachable,
        detail:
            'Hop $viaNumber closed the connection without answering. It '
            'cannot reach hop $targetNumber.',
        hopsProbed: viaNumber,
        failingHopNumber: targetNumber,
      );
    }

    // Drain the rest of the response headers so the socket is positioned at
    // the start of the tunnel for the next hop.
    final status = _statusCodeOf(statusLine);
    if (status != null && status >= 200 && status < 300) {
      await reader.drainHeaders();
      return PreflightResult(
        outcome: PreflightOutcome.reachable,
        detail: 'Hop $targetNumber answered through hop $viaNumber.',
        hopsProbed: targetNumber,
      );
    }

    return _classifyHttpRefusal(
      status: status,
      viaNumber: viaNumber,
      targetNumber: targetNumber,
    );
  }

  PreflightResult _classifyHttpRefusal({
    required int? status,
    required int viaNumber,
    required int targetNumber,
  }) {
    switch (status) {
      case 403:
        return PreflightResult(
          outcome: PreflightOutcome.providerBlocked,
          detail: _providerBlockLine(viaNumber, targetNumber, '403 Forbidden'),
          hopsProbed: viaNumber,
          failingHopNumber: targetNumber,
        );
      case 401:
      case 407:
        return PreflightResult(
          outcome: PreflightOutcome.authenticationFailed,
          detail:
              'Hop $viaNumber rejected its credentials. The account for that '
              'proxy is not valid right now.',
          hopsProbed: viaNumber,
          failingHopNumber: viaNumber,
        );
      default:
        return PreflightResult(
          outcome: PreflightOutcome.hopUnreachable,
          detail:
              'Hop $viaNumber could not reach hop $targetNumber '
              '(${status ?? 'no status'}). Replace hop $targetNumber and try '
              'again.',
          hopsProbed: viaNumber,
          failingHopNumber: targetNumber,
        );
    }
  }

  // --- SOCKS5 ------------------------------------------------------------

  Future<PreflightResult> _socksConnect({
    required Socket socket,
    required _SocketReader reader,
    required int viaNumber,
    required String targetHost,
    required int targetPort,
    required int targetNumber,
    required ProxyCredentials? credentials,
  }) async {
    final hasAuth = credentials != null && credentials.username.isNotEmpty;

    // Greeting: offer no-auth and, when we hold one, username/password.
    socket.add(
      hasAuth
          ? Uint8List.fromList(<int>[0x05, 0x02, 0x00, 0x02])
          : Uint8List.fromList(<int>[0x05, 0x01, 0x00]),
    );
    await socket.flush();

    final greeting = await reader.read(2);
    if (greeting == null || greeting[0] != 0x05) {
      return PreflightResult(
        outcome: PreflightOutcome.hopUnreachable,
        detail:
            'Hop $viaNumber did not answer as a SOCKS5 proxy. Its protocol '
            'or port is wrong.',
        hopsProbed: viaNumber,
        failingHopNumber: viaNumber,
      );
    }

    final method = greeting[1];
    if (method == 0xFF) {
      return PreflightResult(
        outcome: PreflightOutcome.authenticationFailed,
        detail:
            'Hop $viaNumber accepted none of the authentication methods we '
            'offered.',
        hopsProbed: viaNumber,
        failingHopNumber: viaNumber,
      );
    }
    if (method == 0x02) {
      if (!hasAuth) {
        return PreflightResult(
          outcome: PreflightOutcome.authenticationFailed,
          detail:
              'Hop $viaNumber requires a username and password, and none was '
              'supplied for it.',
          hopsProbed: viaNumber,
          failingHopNumber: viaNumber,
        );
      }
      final user = _ascii(credentials.username);
      final pass = _ascii(credentials.password);
      socket.add(
        Uint8List.fromList(<int>[
          0x01,
          user.length,
          ...user,
          pass.length,
          ...pass,
        ]),
      );
      await socket.flush();
      final authReply = await reader.read(2);
      if (authReply == null || authReply[1] != 0x00) {
        return PreflightResult(
          outcome: PreflightOutcome.authenticationFailed,
          detail:
              'Hop $viaNumber rejected its credentials. The account for that '
              'proxy is not valid right now.',
          hopsProbed: viaNumber,
          failingHopNumber: viaNumber,
        );
      }
    }

    // CONNECT, addressed by name so the hop resolves it rather than us.
    final host = _ascii(targetHost);
    socket.add(
      Uint8List.fromList(<int>[
        0x05, 0x01, 0x00, 0x03,
        host.length, ...host,
        (targetPort >> 8) & 0xFF, targetPort & 0xFF,
      ]),
    );
    await socket.flush();

    final reply = await reader.read(4);
    if (reply == null) {
      return PreflightResult(
        outcome: PreflightOutcome.hopUnreachable,
        detail:
            'Hop $viaNumber closed the connection without answering. It '
            'cannot reach hop $targetNumber.',
        hopsProbed: viaNumber,
        failingHopNumber: targetNumber,
      );
    }

    final code = reply[1];
    if (code != 0x00) {
      return _classifySocksRefusal(
        code: code,
        viaNumber: viaNumber,
        targetNumber: targetNumber,
      );
    }

    // Success: consume the bound address so the socket is positioned at the
    // start of the tunnel.
    final consumed = await _consumeSocksAddress(reader, reply[3]);
    if (!consumed) {
      return PreflightResult(
        outcome: PreflightOutcome.hopUnreachable,
        detail:
            'Hop $viaNumber answered with a reply this client could not '
            'read.',
        hopsProbed: viaNumber,
        failingHopNumber: viaNumber,
      );
    }

    return PreflightResult(
      outcome: PreflightOutcome.reachable,
      detail: 'Hop $targetNumber answered through hop $viaNumber.',
      hopsProbed: targetNumber,
    );
  }

  PreflightResult _classifySocksRefusal({
    required int code,
    required int viaNumber,
    required int targetNumber,
  }) {
    // 0x02 is "connection not allowed by ruleset" — the SOCKS5 spelling of
    // the same provider policy that answers 403 over HTTP (P25).
    if (code == 0x02) {
      return PreflightResult(
        outcome: PreflightOutcome.providerBlocked,
        detail: _providerBlockLine(
          viaNumber,
          targetNumber,
          'SOCKS5 "not allowed by ruleset"',
        ),
        hopsProbed: viaNumber,
        failingHopNumber: targetNumber,
      );
    }
    return PreflightResult(
      outcome: PreflightOutcome.hopUnreachable,
      detail:
          'Hop $viaNumber could not reach hop $targetNumber '
          '(${_socksReplyName(code)}). Replace hop $targetNumber and try '
          'again.',
      hopsProbed: viaNumber,
      failingHopNumber: targetNumber,
    );
  }

  Future<bool> _consumeSocksAddress(_SocketReader reader, int type) async {
    switch (type) {
      case 0x01:
        return await reader.read(4 + 2) != null;
      case 0x04:
        return await reader.read(16 + 2) != null;
      case 0x03:
        final length = await reader.read(1);
        if (length == null) return false;
        return await reader.read(length[0] + 2) != null;
      default:
        return false;
    }
  }

  // --- shared ------------------------------------------------------------

  /// The P25 sentence. One line, names the cause, and says what to change.
  ///
  /// Deliberately not "connection failed": the operator lost real time
  /// discovering that this refusal is a provider policy and not a dead hop,
  /// and nothing about the generic wording would have led them there.
  static String _providerBlockLine(
    int viaNumber,
    int targetNumber,
    String refusal,
  ) =>
      'Hop $viaNumber refused to connect to hop $targetNumber ($refusal) — '
      'providers block connections back into their own IP ranges, so two '
      'hops from the same provider cannot chain. Give hop $targetNumber a '
      'proxy from a different provider.';

  static int? _statusCodeOf(String statusLine) {
    final parts = statusLine.trim().split(RegExp(r'\s+'));
    if (parts.length < 2) return null;
    return int.tryParse(parts[1]);
  }

  static String _socksReplyName(int code) => switch (code) {
    0x01 => 'general failure',
    0x03 => 'network unreachable',
    0x04 => 'host unreachable',
    0x05 => 'connection refused',
    0x06 => 'TTL expired',
    0x07 => 'command not supported',
    0x08 => 'address type not supported',
    _ => 'reply 0x${code.toRadixString(16)}',
  };

  static Uint8List _ascii(String value) =>
      Uint8List.fromList(value.codeUnits.map((c) => c & 0xFF).toList());

  /// Local base64 so this file does not pull in `dart:convert` just for one
  /// header, and so the encoded value never passes through a logging-aware
  /// codec.
  static String base64Encode(String value) {
    const alphabet =
        'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
    final bytes = _ascii(value);
    final out = StringBuffer();
    for (var i = 0; i < bytes.length; i += 3) {
      final b0 = bytes[i];
      final b1 = i + 1 < bytes.length ? bytes[i + 1] : 0;
      final b2 = i + 2 < bytes.length ? bytes[i + 2] : 0;
      out
        ..write(alphabet[b0 >> 2])
        ..write(alphabet[((b0 & 0x03) << 4) | (b1 >> 4)])
        ..write(i + 1 < bytes.length ? alphabet[((b1 & 0x0F) << 2) | (b2 >> 6)] : '=')
        ..write(i + 2 < bytes.length ? alphabet[b2 & 0x3F] : '=');
    }
    return out.toString();
  }
}

/// Buffers a socket so the probe can ask for exactly N bytes or one line.
///
/// A [Socket] is a single-subscription stream, and the walk needs several
/// sequential reads over the same connection, so the subscription is taken
/// once and the bytes are queued here.
class _SocketReader {
  _SocketReader(Socket socket) {
    _subscription = socket.listen(
      (Uint8List data) {
        _buffer.addAll(data);
        _wake();
      },
      onError: (Object _) => _close(),
      onDone: _close,
      cancelOnError: true,
    );
  }

  final List<int> _buffer = <int>[];
  late final StreamSubscription<Uint8List> _subscription;
  bool _done = false;
  Completer<void>? _waiter;

  void _close() {
    _done = true;
    _wake();
  }

  void _wake() {
    final waiter = _waiter;
    _waiter = null;
    if (waiter != null && !waiter.isCompleted) waiter.complete();
  }

  /// Blocks until more bytes arrive or the peer closes. Returns false once
  /// nothing more can arrive.
  Future<bool> _awaitMore() async {
    if (_done) return false;
    final waiter = Completer<void>();
    _waiter = waiter;
    await waiter.future;
    return true;
  }

  /// Returns exactly [count] bytes, or null if the peer closed first.
  Future<List<int>?> read(int count) async {
    while (_buffer.length < count) {
      if (!await _awaitMore()) return null;
    }
    final out = _buffer.sublist(0, count);
    _buffer.removeRange(0, count);
    return out;
  }

  /// Reads up to and including the next CRLF, returning the line without it.
  Future<String?> readLine() async {
    while (true) {
      final index = _indexOfCrlf();
      if (index >= 0) {
        final line = String.fromCharCodes(_buffer.sublist(0, index));
        _buffer.removeRange(0, index + 2);
        return line;
      }
      if (!await _awaitMore()) return null;
    }
  }

  /// Consumes response headers up to the blank line.
  Future<void> drainHeaders() async {
    while (true) {
      final line = await readLine();
      if (line == null || line.isEmpty) return;
    }
  }

  int _indexOfCrlf() {
    for (var i = 0; i + 1 < _buffer.length; i++) {
      if (_buffer[i] == 0x0D && _buffer[i + 1] == 0x0A) return i;
    }
    return -1;
  }

  Future<void> dispose() async {
    try {
      await _subscription.cancel();
    } on Object {
      // Already cancelled.
    }
  }
}
