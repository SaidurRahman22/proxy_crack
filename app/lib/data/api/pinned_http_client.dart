import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';

import '../../core/net/host_pin.dart';

/// An HTTP client that dials [HostPin]'s pinned address while leaving the
/// request URL — and therefore TLS and the `Host` header — untouched.
///
/// ## Why a connection factory rather than rewriting the URL
///
/// The naive version of "use the resolved IP" is to swap the hostname out of
/// the URL for the address. That works over cleartext and **fails over
/// TLS**, which is the only place it matters:
///
/// * the certificate is issued for `api.example.com`, so validating it
///   against `203.0.113.10` fails — unless the certificate happens to carry
///   an IP SAN, which public CAs rarely issue;
/// * SNI would carry the address, so a host that serves several names from
///   one address (every CDN, every shared ingress) answers with the wrong
///   certificate or with a default vhost;
/// * the `Host` header would carry the address, which Django rejects with a
///   400 `DisallowedHost` before a view ever runs.
///
/// Each of those appears **only in production**, because the dev base URL is
/// a bare IP over cleartext. A DNS fix that ships as a TLS outage visible
/// nowhere but production is a bad trade.
///
/// So the URL is never touched. `HttpClient.connectionFactory` is given the
/// request URI and returns the socket to use, which lets the TCP connection
/// go to the pinned address while every name-carrying part of the request
/// still says `api.example.com`:
///
/// | carries | value |
/// |---|---|
/// | TCP destination | the pinned address |
/// | TLS SNI | the hostname |
/// | certificate validation | the hostname |
/// | `Host` header | the hostname (dart:io derives it from the URI) |
///
/// ## ⚠️ The factory must do TLS itself
///
/// Verified in the Dart SDK (`lib/_http/http_impl.dart`,
/// `_ConnectionTarget.connect`): when `connectionFactory` is set, dart:io
/// uses the returned socket **as-is**. The `SecureSocket.startConnect` call
/// it would otherwise make for an `https` URL lives in the `else` branch and
/// is skipped entirely. A factory that returns a plain [Socket] for an
/// `https` request therefore speaks HTTP in clear text to a TLS port — the
/// request fails, but it fails *after* the bytes have left the device.
///
/// This factory secures the socket itself with
/// `SecureSocket.secure(..., host: url.host)`, whose `host` argument is
/// documented as the name used for both SNI and certificate validation, and
/// which performs no lookup of its own.
///
/// Nothing here relaxes validation: no `onBadCertificate`, no custom
/// [SecurityContext]. Passing a null context uses
/// `SecurityContext.defaultContext` — the same roots the unpinned client
/// uses — so a certificate that would have been rejected without the pin is
/// still rejected with it.
http.Client createPinnedHttpClient(HostPin pin) =>
    IOClient(pinnedHttpClient(pin));

/// The `dart:io` client underneath [createPinnedHttpClient].
///
/// Separated so a test can drive the connection factory directly.
HttpClient pinnedHttpClient(HostPin pin) {
  final client = HttpClient();
  client.connectionFactory = (Uri url, String? proxyHost, int? proxyPort) =>
      _connect(pin, url, proxyHost, proxyPort);
  return client;
}

Future<ConnectionTask<Socket>> _connect(
  HostPin pin,
  Uri url,
  String? proxyHost,
  int? proxyPort,
) async {
  if (proxyHost != null && proxyPort != null) {
    // A system HTTP proxy is configured. The socket goes to the proxy, and
    // dart:io then builds a CONNECT tunnel and secures *that* itself, so
    // this must hand back a plain socket and must not pin anything: the
    // name being resolved is the proxy's, not ours.
    return Socket.startConnect(proxyHost, proxyPort);
  }

  // `addressFor` answers null for every host but the pinned one, so an
  // unpinned client — a dev build against `10.0.2.2`, a desktop build, a
  // test — behaves exactly as it did before this file existed.
  final pinned = pin.addressFor(url.host);
  final task = await Socket.startConnect(pinned ?? url.host, url.port);

  if (!url.isScheme('https')) return task;

  final secured = task.socket.then((raw) async {
    try {
      // `host` is what goes into SNI and what the certificate is checked
      // against. It is the hostname from the URL, never the pinned address.
      return await SecureSocket.secure(raw, host: url.host);
    } on Object {
      // A handshake that fails must not leave the TCP socket open.
      raw.destroy();
      rethrow;
    }
  });
  return ConnectionTask.fromSocket<Socket>(secured, task.cancel);
}
