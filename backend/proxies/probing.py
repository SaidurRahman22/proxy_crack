"""Probers: the thing that actually finds out whether a proxy works.

proxychains discovers a dead hop by failing on it. The whole point of this
module is to find out first, and to find out the *right* thing.

A TCP handshake against the proxy's own port is not a health check. It
proves a socket is listening, which a dead Shadowsocks container, a proxy
whose upstream credentials expired, and a provider who silently stopped
routing your traffic will all happily do. So `SocketProber` performs the
full protocol handshake and a real **CONNECT to an external target**, and
times the whole round trip. That number is what a user would experience,
and a failure is a failure the user would have hit.

Two implementations, by design:

* `SocketProber` -- the real one. Speaks SOCKS5 (RFC 1928/1929), HTTP
  CONNECT, and Shadowsocks AEAD (`proxies.shadowsocks`), which is all
  three protocols in D4.
* `FakeProber` -- deterministic, scripted, no sockets. **Every test in this
  codebase uses this one.** Tests that need real proxies are tests that
  fail in CI, fail on a plane, and fail in the sandbox this project is
  developed in.

Threading contract
------------------
`probe_batch` runs probers in a thread pool, and **a prober must never
touch the database**. It is handed a `ProxyTarget`, a plain frozen
dataclass built on the main thread (which is also where the credential
columns get decrypted). The rows go back to the main thread to be written.

That rule buys two things: no connection-per-thread lifecycle to get
wrong, and no chance of a probe thread holding a write lock on SQLite
while it waits ten seconds for a timeout.
"""

import abc
import base64
import logging
import socket
import ssl
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from django.conf import settings
from django.utils.module_loading import import_string

from proxies.models import ProbeErrorKind, ProxyProtocol
from proxies.shadowsocks import (
    DecryptionFailed,
    ShadowsocksError,
    ShadowsocksSession,
    UnsupportedCipher,
    cipher_spec,
)

logger = logging.getLogger("proxycrack.probing")

__all__ = [
    "FakeProber",
    "ProbeResult",
    "ProbeUnsupported",
    "Prober",
    "ProxyTarget",
    "SHADOWSOCKS_SILENCE",
    "SocketProber",
    "get_prober",
    "probe_batch",
    "probe_destination",
]

DEFAULT_TIMEOUT = 10.0
DEFAULT_CONCURRENCY = 8


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    """One observation. `latency_ms` is meaningful only on success.

    A failed probe deliberately carries no latency. Recording the timeout
    duration would put a 10,000 ms sample into the percentile maths every
    time a proxy was unreachable, which would make p95 a measure of the
    timeout setting rather than of the proxy.
    """

    succeeded: bool
    latency_ms: Optional[int] = None
    error_kind: str = ""
    detail: str = ""

    @classmethod
    def ok(cls, latency_ms):
        return cls(succeeded=True, latency_ms=int(latency_ms))

    @classmethod
    def failed(cls, error_kind, detail=""):
        return cls(succeeded=False, error_kind=error_kind, detail=detail[:200])


@dataclass(frozen=True)
class ProxyTarget:
    """A database-free snapshot of a proxy, safe to hand to a thread."""

    id: int
    protocol: str
    host: str
    port: int
    username: str = ""
    password: str = ""
    ss_method: str = ""

    @classmethod
    def from_proxy(cls, proxy):
        """Built on the main thread: reading these fields decrypts them."""
        return cls(
            id=proxy.pk,
            protocol=str(proxy.protocol),
            host=proxy.host,
            port=int(proxy.port),
            username=proxy.username or "",
            password=proxy.password or "",
            ss_method=proxy.ss_method or "",
        )

    def __str__(self):
        return f"{self.protocol}://{self.host}:{self.port}"


class ProbeUnsupported(Exception):
    """This prober cannot honestly probe this proxy.

    Raised rather than returning a failure, because a failure result would
    mark a perfectly good proxy dead. The run counts these as skipped and
    writes no probe row, leaving the proxy in the "unprobed" state, which
    is the truth.

    `code` groups the reason so a run can report "9 skipped: 9 SS2022"
    rather than nine prose strings. It is **our** gap in every case -- a
    skip never says anything about the proxy.
    """

    #: Reason codes. Stable; the admin API and the command both report them.
    UNSUPPORTED_PROTOCOL = "unsupported_protocol"
    UNSUPPORTED_CIPHER = "unsupported_cipher"

    def __init__(self, message, code=UNSUPPORTED_PROTOCOL):
        super().__init__(message)
        self.code = code


def probe_destination() -> Tuple[str, int]:
    """The external endpoint probes CONNECT to.

    Defaults to a TLS port on a host that is up essentially always. Port
    443 rather than 80 because many proxies allow CONNECT only to 443, and
    a probe that a correctly-configured proxy refuses is a probe that
    measures our own configuration.
    """
    host = getattr(settings, "PROXY_PROBE_TARGET_HOST", "www.google.com")
    port = int(getattr(settings, "PROXY_PROBE_TARGET_PORT", 443))
    return host, port


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class Prober(abc.ABC):
    """Opens a connection through a proxy and reports what happened."""

    @abc.abstractmethod
    def probe(
        self,
        target: ProxyTarget,
        *,
        destination: Tuple[str, int],
        timeout: float,
    ) -> ProbeResult:
        """Must not raise for a network condition -- return a failed
        `ProbeResult` instead. May raise `ProbeUnsupported`.
        """


# ---------------------------------------------------------------------------
# The real one
# ---------------------------------------------------------------------------


class SocketProber(Prober):
    """A genuine CONNECT through the proxy, timed end to end.

    One deadline covers the whole exchange rather than a timeout per
    socket operation. A proxy that trickles one byte per nine seconds
    would otherwise keep the probe alive indefinitely while never failing
    any individual read, which is precisely the kind of proxy worth
    detecting.

    All three protocols in D4 are spoken. Shadowsocks is a full AEAD
    client -- see `_probe_shadowsocks` and `proxies.shadowsocks`.
    """

    def handler_for(self, protocol, target=None):
        """The handshake for a protocol, or `ProbeUnsupported`.

        Resolved **before any socket is opened**. Discovering that a
        proxy is unprobeable after connecting would spend a TCP
        connection per such proxy per run to learn something known
        statically -- and would make this method impossible to test
        without a network.

        `target` is optional and is used only for the Shadowsocks cipher
        check, which needs the row rather than just the protocol: an
        `aes-256-gcm` proxy is probeable and a `2022-blake3-aes-256-gcm`
        one is not, and both are `protocol="ss"`.
        """
        try:
            handler = {
                ProxyProtocol.SOCKS5.value: self._probe_socks5,
                ProxyProtocol.HTTP.value: self._probe_http_connect,
                ProxyProtocol.SHADOWSOCKS.value: self._probe_shadowsocks,
            }[protocol]
        except KeyError:
            raise ProbeUnsupported(
                f"no prober for protocol {protocol!r}",
                ProbeUnsupported.UNSUPPORTED_PROTOCOL,
            )

        if protocol == ProxyProtocol.SHADOWSOCKS.value and target is not None:
            # Validated here, not at connect time, so an inventory full of
            # SS2022 proxies costs zero TCP connections per run.
            try:
                cipher_spec(target.ss_method)
            except UnsupportedCipher as exc:
                raise ProbeUnsupported(
                    str(exc), ProbeUnsupported.UNSUPPORTED_CIPHER
                )

        return handler

    def probe(self, target, *, destination, timeout):
        handler = self.handler_for(target.protocol, target)

        started = time.monotonic()
        deadline = started + timeout
        sock = None
        try:
            sock = socket.create_connection(
                (target.host, target.port), timeout=timeout
            )
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            handler(sock, target, destination, deadline)
        except ProbeUnsupported:
            raise
        except _ProbeFailure as exc:
            return ProbeResult.failed(exc.kind, exc.detail)
        except (socket.timeout, TimeoutError):
            return ProbeResult.failed(ProbeErrorKind.TIMEOUT, "connect timed out")
        except ConnectionRefusedError:
            return ProbeResult.failed(ProbeErrorKind.REFUSED, "connection refused")
        except socket.gaierror as exc:
            return ProbeResult.failed(ProbeErrorKind.DNS, str(exc))
        except ssl.SSLError as exc:
            return ProbeResult.failed(ProbeErrorKind.TLS, str(exc))
        except (ConnectionResetError, BrokenPipeError) as exc:
            # The peer accepted a connection and then tore it down mid
            # handshake. That is a protocol-level refusal, not an
            # unreachable host.
            return ProbeResult.failed(ProbeErrorKind.PROTOCOL, str(exc))
        except OSError as exc:
            return ProbeResult.failed(ProbeErrorKind.UNKNOWN, str(exc))
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

        return ProbeResult.ok(round((time.monotonic() - started) * 1000))

    # -- SOCKS5 (RFC 1928, auth RFC 1929) --------------------------------

    def _probe_socks5(self, sock, target, destination, deadline):
        use_auth = bool(target.username or target.password)
        methods = b"\x00\x02" if use_auth else b"\x00"
        _send(sock, bytes([0x05, len(methods)]) + methods, deadline)

        version, method = _recv_exactly(sock, 2, deadline)
        if version != 0x05:
            raise _ProbeFailure(
                ProbeErrorKind.PROTOCOL, f"socks version {version} in greeting"
            )
        if method == 0xFF:
            raise _ProbeFailure(
                ProbeErrorKind.AUTH_FAILED, "no acceptable authentication method"
            )
        if method == 0x02:
            self._socks5_userpass(sock, target, deadline)
        elif method != 0x00:
            raise _ProbeFailure(
                ProbeErrorKind.PROTOCOL, f"unsupported auth method {method}"
            )

        host, port = destination
        encoded = host.encode("idna")
        if len(encoded) > 255:
            raise _ProbeFailure(ProbeErrorKind.PROTOCOL, "destination host too long")
        request = (
            b"\x05\x01\x00\x03"
            + bytes([len(encoded)])
            + encoded
            + struct.pack("!H", port)
        )
        _send(sock, request, deadline)

        reply = _recv_exactly(sock, 4, deadline)
        if reply[0] != 0x05:
            raise _ProbeFailure(
                ProbeErrorKind.PROTOCOL, f"socks version {reply[0]} in reply"
            )
        if reply[1] != 0x00:
            raise _ProbeFailure(
                SOCKS5_REPLY_ERRORS.get(reply[1], ProbeErrorKind.UNKNOWN),
                f"socks5 reply code {reply[1]}",
            )

        # Drain the bound address so the probe fails here rather than
        # "succeeding" against a proxy that sent a truncated reply.
        atyp = reply[3]
        if atyp == 0x01:
            _recv_exactly(sock, 4 + 2, deadline)
        elif atyp == 0x04:
            _recv_exactly(sock, 16 + 2, deadline)
        elif atyp == 0x03:
            length = _recv_exactly(sock, 1, deadline)[0]
            _recv_exactly(sock, length + 2, deadline)
        else:
            raise _ProbeFailure(
                ProbeErrorKind.PROTOCOL, f"unknown address type {atyp} in reply"
            )

    def _socks5_userpass(self, sock, target, deadline):
        user = target.username.encode("utf-8")
        secret = target.password.encode("utf-8")
        if len(user) > 255 or len(secret) > 255:
            raise _ProbeFailure(
                ProbeErrorKind.AUTH_FAILED, "credential too long for RFC 1929"
            )
        _send(
            sock,
            b"\x01" + bytes([len(user)]) + user + bytes([len(secret)]) + secret,
            deadline,
        )
        _version, status = _recv_exactly(sock, 2, deadline)
        if status != 0x00:
            raise _ProbeFailure(
                ProbeErrorKind.AUTH_FAILED, f"auth rejected with status {status}"
            )

    # -- HTTP CONNECT ----------------------------------------------------

    def _probe_http_connect(self, sock, target, destination, deadline):
        host, port = destination
        authority = f"{host}:{port}"
        lines = [
            f"CONNECT {authority} HTTP/1.1",
            f"Host: {authority}",
            "Proxy-Connection: close",
        ]
        if target.username or target.password:
            token = base64.b64encode(
                f"{target.username}:{target.password}".encode("utf-8")
            ).decode("ascii")
            lines.append(f"Proxy-Authorization: Basic {token}")
        _send(sock, ("\r\n".join(lines) + "\r\n\r\n").encode("ascii"), deadline)

        status_line = _recv_line(sock, deadline)
        parts = status_line.split(None, 2)
        if len(parts) < 2 or not parts[0].upper().startswith("HTTP/"):
            raise _ProbeFailure(
                ProbeErrorKind.PROTOCOL, f"bad status line {status_line[:80]!r}"
            )
        try:
            code = int(parts[1])
        except ValueError:
            raise _ProbeFailure(
                ProbeErrorKind.PROTOCOL, f"bad status code in {status_line[:80]!r}"
            )

        if 200 <= code < 300:
            return
        if code in (401, 403, 407):
            raise _ProbeFailure(
                ProbeErrorKind.AUTH_FAILED, f"proxy returned {code}"
            )
        if code in (504, 522, 524):
            raise _ProbeFailure(ProbeErrorKind.TIMEOUT, f"proxy returned {code}")
        if code in (502, 503):
            raise _ProbeFailure(ProbeErrorKind.REFUSED, f"proxy returned {code}")
        raise _ProbeFailure(ProbeErrorKind.PROTOCOL, f"proxy returned {code}")

    # -- Shadowsocks AEAD ------------------------------------------------

    def _probe_shadowsocks(self, sock, target, destination, deadline):
        """A real AEAD round trip, because there is no handshake to watch.

        What the other two handlers get for free, this one has to
        manufacture. SOCKS5 and HTTP both answer a greeting, so observing
        a reply is enough. A Shadowsocks server answers nothing it cannot
        decrypt and sends no banner even when it can, so the only evidence
        that the credentials are right is a reply that **authenticates
        under our own key** -- which in turn requires the server to have
        actually connected onward to the destination and got something
        back. That makes this the strictest of the three probes: it proves
        the password, the cipher, and the upstream path in one exchange.

        The exchange:

            -> [our salt][sealed: SOCKS5 address of the destination]
            -> [sealed: a real TLS ClientHello for that destination]
            <- [server salt][sealed: whatever the destination replied]

        and the probe succeeds only if that last line decrypts.
        """
        session = ShadowsocksSession(target.password, target.ss_method)

        host, port = destination
        try:
            request = session.request(host, port)
        except ValueError as exc:
            raise _ProbeFailure(ProbeErrorKind.PROTOCOL, str(exc))

        # Header and payload are sealed in one call so they share a write
        # and usually one segment. A server that reads the address block
        # and then waits for more data gets it immediately rather than
        # after another round trip.
        _send(sock, session.seal(request + _tls_client_hello(host)), deadline)

        try:
            session.begin_read(_recv_exactly(sock, session.salt_size, deadline))
            length = session.open_length(
                _recv_exactly(sock, session.length_block_size, deadline)
            )
            session.open_payload(
                _recv_exactly(sock, length + session.tag_size, deadline), length
            )
        except DecryptionFailed as exc:
            # The one case Shadowsocks *does* let us call: the server
            # replied, and the reply does not authenticate. Nothing but a
            # wrong key, a wrong cipher, or an endpoint that is not a
            # Shadowsocks server produces that.
            raise _ProbeFailure(
                ProbeErrorKind.AUTH_FAILED,
                f"{exc} -- wrong password, wrong cipher, or not a "
                f"shadowsocks server",
            )
        except ShadowsocksError as exc:
            raise _ProbeFailure(ProbeErrorKind.PROTOCOL, str(exc))
        except (
            _ProbeFailure,
            ConnectionResetError,
            BrokenPipeError,
            socket.timeout,
            TimeoutError,
            OSError,
        ) as exc:
            # Silence, in all the shapes it arrives in: a clean EOF, the
            # deadline expiring, or an abrupt RST. A Shadowsocks server
            # that cannot decrypt us is *supposed* to be indistinguishable
            # from one that is merely broken, and it discards, hangs up or
            # resets at its own discretion -- so none of these three can
            # be read as a cause.
            #
            # They are deliberately collapsed to one kind. Letting an RST
            # surface as `protocol` and a timeout as `timeout` would hand
            # an operator two different-looking verdicts for one
            # indistinguishable situation, which is worse than saying
            # plainly that we do not know. See SHADOWSOCKS_SILENCE.
            detail = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
            raise _ProbeFailure(
                ProbeErrorKind.UNVERIFIABLE,
                f"{SHADOWSOCKS_SILENCE} (underlying: {detail})",
            )


# The honest limit of a Shadowsocks probe, stated once so the error-kind
# mapping can point at it instead of paraphrasing.
#
# Shadowsocks is deliberately built so that an active prober learns
# nothing. A server that cannot decrypt the first chunk does not reply, does
# not reset, and does not error -- it reads and discards, or it hangs up
# exactly as a server under load would. So when we send a request and get
# back nothing, these are all indistinguishable on the wire:
#
#   * the password is wrong
#   * the cipher is wrong
#   * the server stopped forwarding to the internet an hour ago
#   * the port is answered by something that is not Shadowsocks at all
#
# A probe that reported `auth_failed` for that silence would be inventing a
# cause, and `timeout` would imply the host is unreachable when the TCP
# connection plainly succeeded. Both would be lies of exactly the kind this
# module exists to avoid, so there is a dedicated error kind for "we got
# nothing back and the protocol does not let us say why".
#
# What *is* distinguishable, and is reported precisely:
#
#   * the TCP connection failing       -> refused / timeout / dns
#     (an unreachable host, before any Shadowsocks logic runs at all)
#   * a reply that does not decrypt    -> auth_failed
#     (only a wrong key or a non-Shadowsocks endpoint does this)
#   * a decryptable reply              -> success, credentials proven
SHADOWSOCKS_SILENCE = (
    "no decryptable reply: shadowsocks servers answer nothing they cannot "
    "decrypt, so a wrong password and a server that has stopped forwarding "
    "are identical on the wire. The TCP connection itself succeeded"
)


def _tls_client_hello(host):
    """A genuine TLS ClientHello for `host`, built without a socket.

    Something has to be sent through the tunnel, because the proof we
    need is a *reply* and no reply arrives until the destination has
    something to answer. A real ClientHello is used rather than a
    hand-rolled byte string so the destination behaves exactly as it
    would for a user: correct record version, correct extensions, and an
    SNI, which a modern host needs before it will answer at all.

    No certificate is verified. The probe is asking "did my traffic reach
    a real server and come back", not "is this the authentic google.com";
    trust decisions belong to the client at connect time, and failing a
    probe over a corporate TLS interceptor would measure the network the
    *prober* sits in.
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    tls = context.wrap_bio(incoming, outgoing, server_hostname=host)
    try:
        tls.do_handshake()
    except ssl.SSLWantReadError:
        pass  # Expected: the peer's half has not arrived and never will.
    except ssl.SSLError:
        # An SNI the TLS stack refuses (an IP literal on some builds).
        # A bare HTTP request still elicits a reply from essentially
        # anything listening, which is all this payload has to do.
        return f"HEAD / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()
    return outgoing.read()


SOCKS5_REPLY_ERRORS = {
    0x01: ProbeErrorKind.UNKNOWN,  # general SOCKS server failure
    0x02: ProbeErrorKind.AUTH_FAILED,  # connection not allowed by ruleset
    0x03: ProbeErrorKind.UNKNOWN,  # network unreachable
    0x04: ProbeErrorKind.DNS,  # host unreachable
    0x05: ProbeErrorKind.REFUSED,  # connection refused
    0x06: ProbeErrorKind.TIMEOUT,  # TTL expired
    0x07: ProbeErrorKind.PROTOCOL,  # command not supported
    0x08: ProbeErrorKind.PROTOCOL,  # address type not supported
}


class _ProbeFailure(Exception):
    """Internal: a protocol-level refusal, carrying its error kind."""

    def __init__(self, kind, detail=""):
        super().__init__(detail or str(kind))
        self.kind = str(kind)
        self.detail = detail


def _remaining(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise _ProbeFailure(ProbeErrorKind.TIMEOUT, "deadline exceeded")
    return left


def _send(sock, payload, deadline):
    sock.settimeout(_remaining(deadline))
    sock.sendall(payload)


def _recv_exactly(sock, count, deadline):
    buffer = bytearray()
    while len(buffer) < count:
        sock.settimeout(_remaining(deadline))
        chunk = sock.recv(count - len(buffer))
        if not chunk:
            raise _ProbeFailure(
                ProbeErrorKind.PROTOCOL, "proxy closed the connection mid-handshake"
            )
        buffer.extend(chunk)
    return bytes(buffer)


def _recv_line(sock, deadline, limit=8192):
    buffer = bytearray()
    while b"\r\n" not in buffer:
        if len(buffer) > limit:
            raise _ProbeFailure(ProbeErrorKind.PROTOCOL, "status line too long")
        sock.settimeout(_remaining(deadline))
        chunk = sock.recv(1024)
        if not chunk:
            raise _ProbeFailure(
                ProbeErrorKind.PROTOCOL, "proxy closed the connection mid-handshake"
            )
        buffer.extend(chunk)
    return buffer.split(b"\r\n", 1)[0].decode("latin-1")


# ---------------------------------------------------------------------------
# The fake one -- used by every test
# ---------------------------------------------------------------------------


class FakeProber(Prober):
    """Deterministic prober. No sockets, no clock, no network.

    Scripted per proxy id:

        FakeProber({
            7: ProbeResult.ok(12),                      # always
            8: [fail, fail, ProbeResult.ok(30)],        # in order, last repeats
            9: lambda target: ...,                      # computed
        }, default=ProbeResult.ok(100))

    A list is consumed one entry per call and its final entry repeats, which
    is what makes "three failures in a row then a success" expressible --
    the exact shape the flapping-suppression rule has to be tested against.

    `calls` records every probe in order, so a test can assert that a run
    scoped to one tier really did not touch the others.
    """

    def __init__(self, results=None, default=None, unsupported=()):
        self.results: Dict[int, object] = dict(results or {})
        self.default = default if default is not None else ProbeResult.ok(50)
        self.unsupported = set(unsupported)
        self.calls: List[int] = []
        self._lock = threading.Lock()

    def probe(self, target, *, destination, timeout):
        with self._lock:
            self.calls.append(target.id)
            if target.protocol in self.unsupported or target.id in self.unsupported:
                raise ProbeUnsupported(f"fake: {target.protocol} unsupported")
            scripted = self.results.get(target.id, self.default)
            if isinstance(scripted, list):
                if not scripted:
                    return self.default
                return scripted.pop(0) if len(scripted) > 1 else scripted[0]
        # Deliberately outside the lock: a callable is caller-supplied code
        # and holding the lock across it would let one script deadlock the
        # pool. Only the mutable `calls` list and the scripted sequences
        # need protecting, and both are done with by here.
        if callable(scripted):
            return scripted(target)
        return scripted


def get_prober() -> Prober:
    """Instantiate the configured prober.

    `settings.PROXY_PROBER` is a dotted path so a test can swap in
    `FakeProber` with `override_settings` and nothing has to thread a
    prober argument through the command, the view and the batch runner.
    """
    path = getattr(settings, "PROXY_PROBER", "proxies.probing.SocketProber")
    return import_string(path)()


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------


@dataclass
class BatchOutcome:
    """What a batch produced. `results` is ready for `record_probe_results`."""

    results: List[Tuple[int, ProbeResult, str]] = field(default_factory=list)
    skipped: List[Tuple[int, str]] = field(default_factory=list)
    errored: List[Tuple[int, str]] = field(default_factory=list)
    #: proxy id -> `ProbeUnsupported.code`. Parallel to `skipped`, which
    #: stays a 2-tuple of (id, prose) because the admin API and the
    #: command are both written against that shape. The codes are what
    #: makes "4 skipped" answerable as "4 SS2022, 0 unknown protocols" --
    #: one of those is a roadmap item and the other is a data problem.
    skipped_codes: Dict[int, str] = field(default_factory=dict)

    @property
    def succeeded(self):
        return sum(1 for _pk, result, _t in self.results if result.succeeded)

    @property
    def failed(self):
        return sum(1 for _pk, result, _t in self.results if not result.succeeded)

    def skip_reason_counts(self):
        """`{code: n}` over `skipped`, for a one-line run summary."""
        counts: Dict[str, int] = {}
        for proxy_id, _reason in self.skipped:
            code = self.skipped_codes.get(
                proxy_id, ProbeUnsupported.UNSUPPORTED_PROTOCOL
            )
            counts[code] = counts.get(code, 0) + 1
        return counts


def probe_batch(
    prober: Prober,
    targets: Sequence[ProxyTarget],
    *,
    destination: Optional[Tuple[str, int]] = None,
    timeout: float = DEFAULT_TIMEOUT,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> BatchOutcome:
    """Probe many proxies with bounded concurrency.

    Concurrency is bounded rather than unbounded because the inventory is
    tens of thousands of rows and one thread each would exhaust file
    descriptors long before it exhausted patience. Probing is IO-bound, so
    threads are the right tool and no queue dependency is needed.

    An exception escaping a prober is treated as **our** bug, not as the
    proxy failing: it is counted in `errored` and no probe row is written.
    The alternative -- recording it as a failure -- would mark the entire
    inventory dead the first time someone shipped a typo in the prober.
    """
    destination = destination or probe_destination()
    label = f"{destination[0]}:{destination[1]}"
    outcome = BatchOutcome()
    targets = list(targets)
    if not targets:
        return outcome

    workers = max(1, min(int(concurrency), len(targets)))

    def run(target):
        return target, prober.probe(target, destination=destination, timeout=timeout)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for target, future in [(t, pool.submit(run, t)) for t in targets]:
            try:
                _target, result = future.result()
            except ProbeUnsupported as exc:
                outcome.skipped.append((target.id, str(exc)))
                outcome.skipped_codes[target.id] = getattr(
                    exc, "code", ProbeUnsupported.UNSUPPORTED_PROTOCOL
                )
            except Exception as exc:  # noqa: BLE001 -- see docstring
                logger.exception("prober raised for proxy %s", target.id)
                outcome.errored.append((target.id, f"{type(exc).__name__}: {exc}"))
            else:
                outcome.results.append((target.id, result, label))

    return outcome


def targets_for(queryset: Iterable) -> List[ProxyTarget]:
    """Snapshot a proxy queryset for the thread pool. Main thread only."""
    return [ProxyTarget.from_proxy(proxy) for proxy in queryset]
