"""A Shadowsocks AEAD client, enough of one to prove credentials work.

Shadowsocks is the third protocol in D4 and the only one the prober could
not speak. It is designed to be indistinguishable from random bytes: the
server sends no greeting, no banner, and no reply to anything it cannot
decrypt. There is nothing to observe, so the only honest probe is a real
client -- derive the key, build the request header, encrypt a payload, and
require a reply that authenticates under our own key.

**This module touches no sockets.** It is pure byte transformation, which
is what lets every piece of it be tested against known-answer vectors with
no network and no server. `proxies.probing._probe_shadowsocks` drives it.

The construction, and the three places people get it wrong
----------------------------------------------------------

1. **Master key.** Not a hash of the password. Shadowsocks uses OpenSSL's
   `EVP_BytesToKey` with MD5, no salt and one iteration -- an iterated
   `MD5(previous || password)` chain, taking as many bytes as the cipher's
   key length. Implemented in `evp_bytes_to_key`, verified against the
   OpenSSL CLI itself (see `proxies/test_shadowsocks.py`).

2. **Session subkey.** The master key is never used to encrypt anything.
   Each direction derives `HKDF-SHA1(ikm=master_key, salt=salt,
   info="ss-subkey")` where `salt` is the random prefix that direction
   sent. SHA-1 is not a choice; it is what the protocol specifies, and
   using SHA-256 produces a client that talks to nothing.

3. **Nonce.** A 12-byte **little-endian** counter starting at zero,
   incremented once per AEAD operation -- so twice per chunk, because the
   length and the payload are separately sealed. Read and write keep
   independent counters. Big-endian here is the classic bug: it works for
   the first chunk and fails on the second, which looks like a flaky
   server rather than a broken client.

Framing
-------

    [2-byte length, encrypted][16-byte tag][payload, encrypted][16-byte tag]

The length is big-endian and capped at `MAX_PAYLOAD` (0x3FFF). The cap is
the protocol's, not ours: the two length bytes carry a 14-bit value because
the top two bits are reserved, so a 16-bit length would be accepted by us
and rejected by every real server.

The first thing a client sends, inside that framing, is a SOCKS5-style
address block (ATYP + address + port) naming where the server should
connect -- `socks5_address`.

Ciphers
-------

The three legacy AEAD ciphers commercial providers actually sell. SS2022
(`2022-blake3-*`) is a different construction -- different key derivation,
per-request salts with replay protection, a separate header format -- and
is deliberately **not** implemented. It raises `UnsupportedCipher`, which
the prober turns into a skip rather than a failure: an unsupported cipher
is our gap, not a dead proxy.
"""

import hashlib
import ipaddress
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

__all__ = [
    "CIPHERS",
    "CipherSpec",
    "MAX_PAYLOAD",
    "NONCE_SIZE",
    "NonceCounter",
    "SS_SUBKEY_INFO",
    "ShadowsocksError",
    "ShadowsocksSession",
    "UnsupportedCipher",
    "cipher_spec",
    "derive_subkey",
    "evp_bytes_to_key",
    "is_ss2022",
    "socks5_address",
]


# The info string is part of the wire protocol. Changing it changes the
# subkey and produces a client that can talk to nothing.
SS_SUBKEY_INFO = b"ss-subkey"

NONCE_SIZE = 12

# 0x3FFF, not 0xFFFF. The length field is 14 bits; the top two are
# reserved. A 16-bit length is accepted by this code and rejected by every
# real server, which is a bug that only shows up on large payloads.
MAX_PAYLOAD = 0x3FFF

TAG_SIZE = 16


class ShadowsocksError(Exception):
    """A Shadowsocks stream that did not behave like one."""


class UnsupportedCipher(ShadowsocksError):
    """A cipher this client deliberately does not implement.

    Distinct from every other error in this module because it means
    something different to the caller: the proxy may be perfectly healthy
    and we simply cannot measure it. The prober turns this into a skip,
    never into a failed probe.
    """


class DecryptionFailed(ShadowsocksError):
    """The peer sent bytes that do not authenticate under our key.

    Strong evidence of a wrong password or a wrong cipher -- or that the
    endpoint is not a Shadowsocks server at all. Note the asymmetry that
    defines this protocol: this exception can only be raised when the
    server *replied*. Silence is not evidence of anything; see
    `proxies.probing.SHADOWSOCKS_SILENCE`.
    """


class CipherSpec:
    """One AEAD cipher: its key size and its constructor.

    `salt_size == key_size` is the protocol's rule, not a coincidence, so
    it is derived rather than listed -- a table with both columns is a
    table someone eventually fills in inconsistently.
    """

    __slots__ = ("name", "key_size", "factory")

    def __init__(self, name, key_size, factory):
        self.name = name
        self.key_size = key_size
        self.factory = factory

    @property
    def salt_size(self):
        return self.key_size

    @property
    def tag_size(self):
        return TAG_SIZE

    def aead(self, subkey):
        return self.factory(subkey)

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"<CipherSpec {self.name} key={self.key_size}>"


CIPHERS = {
    spec.name: spec
    for spec in (
        CipherSpec("aes-128-gcm", 16, AESGCM),
        CipherSpec("aes-256-gcm", 32, AESGCM),
        CipherSpec("chacha20-ietf-poly1305", 32, ChaCha20Poly1305),
    )
}

# Spellings providers use for the same three ciphers. Normalised rather
# than rejected: a CSV from a provider who writes `chacha20-poly1305` is
# not a different protocol, and refusing it would silently drop a
# perfectly probeable proxy into the skip bucket.
CIPHER_ALIASES = {
    "aes-128-gcm": "aes-128-gcm",
    "aes-256-gcm": "aes-256-gcm",
    "chacha20-ietf-poly1305": "chacha20-ietf-poly1305",
    # OpenVPN-style spelling seen in provider exports. The IETF 96-bit
    # nonce variant is the only ChaCha20-Poly1305 AEAD Shadowsocks has,
    # so this is a spelling difference and not an ambiguity.
    "chacha20-poly1305": "chacha20-ietf-poly1305",
    "chacha20-ietf-poly1305-ietf": "chacha20-ietf-poly1305",
}

# SS2022. A genuinely different construction (BLAKE3 key derivation,
# per-request salts with server-side replay rejection, a different header
# with a timestamp). Named explicitly so the skip message can say *which*
# gap it is rather than "unknown cipher".
SS2022_PREFIX = "2022-blake3-"


def is_ss2022(method):
    return str(method or "").strip().lower().startswith(SS2022_PREFIX)


def cipher_spec(method):
    """Resolve a cipher name, or explain why we cannot.

    Raises `UnsupportedCipher` for anything not implemented, including the
    empty string -- an `ss` proxy with no cipher recorded is an incomplete
    inventory row, and guessing `aes-256-gcm` for it would produce a
    confident "dead" verdict on a proxy nobody configured.
    """
    name = str(method or "").strip().lower()
    if not name:
        raise UnsupportedCipher(
            "this Shadowsocks proxy has no cipher recorded (ss_method is "
            "empty), so there is nothing to probe with"
        )
    if is_ss2022(name):
        raise UnsupportedCipher(
            f"{name!r} is Shadowsocks 2022, a different construction "
            f"(BLAKE3 key derivation, replay-protected per-request salts). "
            f"This client implements the legacy AEAD ciphers only"
        )
    resolved = CIPHER_ALIASES.get(name)
    if resolved is None:
        raise UnsupportedCipher(
            f"unsupported Shadowsocks cipher {name!r}; this client "
            f"implements {', '.join(sorted(CIPHERS))}"
        )
    return CIPHERS[resolved]


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------


def evp_bytes_to_key(password: bytes, length: int) -> bytes:
    """OpenSSL's `EVP_BytesToKey` with MD5, no salt, one iteration.

    `D_1 = MD5(password)`, `D_i = MD5(D_(i-1) || password)`, and the key is
    the first `length` bytes of `D_1 || D_2 || ...`.

    MD5 is not a security decision here and there is no choice about it:
    it is what Shadowsocks specifies, and any other digest derives a key
    that no server shares. The master key is also never used directly --
    it is HKDF input material, and each session's actual encryption key is
    `derive_subkey`'s output.
    """
    if length <= 0:
        raise ValueError("key length must be positive")
    if not isinstance(password, (bytes, bytearray)):
        raise TypeError("password must be bytes; encode it as UTF-8 first")

    out = bytearray()
    previous = b""
    while len(out) < length:
        previous = hashlib.md5(previous + bytes(password)).digest()
        out.extend(previous)
    return bytes(out[:length])


def derive_subkey(master_key: bytes, salt: bytes, length: int) -> bytes:
    """`HKDF-SHA1(ikm=master_key, salt=salt, info="ss-subkey")`.

    SHA-1 is the protocol's, not a preference. Its weakness is collision
    resistance, which HKDF does not rely on; swapping it for SHA-256 would
    be cryptographically tidier and would produce a client that cannot
    talk to a single real server.
    """
    return HKDF(
        algorithm=hashes.SHA1(),
        length=length,
        salt=salt,
        info=SS_SUBKEY_INFO,
    ).derive(master_key)


# ---------------------------------------------------------------------------
# Nonce
# ---------------------------------------------------------------------------


class NonceCounter:
    """A 12-byte little-endian counter, incremented once per AEAD call.

    Little-endian is load-bearing and is the single most common
    implementation bug in this protocol: the first chunk succeeds either
    way (the counter is zero), and the second one fails. That presents as
    an unreliable server rather than as a broken client, which is how it
    survives review.

    Rollover is defined rather than left to chance: at 2**96 the counter
    wraps to zero. Reaching it would need 2**96 chunks on one connection,
    so this is a correctness statement, not a code path anyone will
    execute -- but an undefined one would be an overflow crash.
    """

    __slots__ = ("_value",)

    def __init__(self, seed: bytes = None, size: int = NONCE_SIZE):
        if seed is None:
            self._value = bytearray(size)
        else:
            if len(seed) != size:
                raise ValueError(f"nonce seed must be {size} bytes")
            self._value = bytearray(seed)

    def current(self) -> bytes:
        return bytes(self._value)

    def advance(self) -> None:
        for index in range(len(self._value)):
            self._value[index] = (self._value[index] + 1) & 0xFF
            if self._value[index]:
                return
        # Every byte wrapped: the counter is back at zero, which is the
        # defined behaviour at 2**96.

    def next(self) -> bytes:
        """The nonce to use now, advancing the counter behind it."""
        value = self.current()
        self.advance()
        return value

    def __eq__(self, other):  # pragma: no cover - test convenience
        if isinstance(other, NonceCounter):
            return self._value == other._value
        return NotImplemented

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"<NonceCounter {self.current().hex()}>"


# ---------------------------------------------------------------------------
# Address block
# ---------------------------------------------------------------------------


def socks5_address(host: str, port: int) -> bytes:
    """A SOCKS5-style target address: ATYP + address + big-endian port.

    A literal IP is sent as one (ATYP 1 / 4) rather than as a hostname, so
    the Shadowsocks server does not perform a pointless DNS lookup of
    "198.51.100.1". Everything else goes out as a domain name (ATYP 3),
    which also means the *remote* side resolves it -- the whole point of
    routing through the proxy.
    """
    if not 0 < int(port) < 65536:
        raise ValueError(f"port out of range: {port}")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        encoded = host.encode("idna")
        if not encoded:
            raise ValueError("empty destination host")
        if len(encoded) > 255:
            raise ValueError("destination host too long for a SOCKS5 address")
        return b"\x03" + bytes([len(encoded)]) + encoded + struct.pack("!H", port)

    if address.version == 4:
        return b"\x01" + address.packed + struct.pack("!H", port)
    return b"\x04" + address.packed + struct.pack("!H", port)


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------


class ShadowsocksSession:
    """One connection's worth of Shadowsocks AEAD state.

    Deliberately split from the socket work. The caller reads exact byte
    counts off the wire and hands them here; this object owns the keys,
    the two nonce counters and the framing, and owns nothing that needs a
    network to exercise.

    Read side usage, which mirrors what the wire gives you:

        session = ShadowsocksSession(password, "aes-256-gcm")
        wire = session.seal(session.request(host, port) + payload)
        ...
        session.begin_read(recv(session.salt_size))
        n = session.open_length(recv(2 + session.tag_size))
        data = session.open_payload(recv(n + session.tag_size), n)
    """

    def __init__(self, password, method, *, salt=None):
        self.spec = cipher_spec(method)
        if isinstance(password, str):
            password = password.encode("utf-8")
        self.master_key = evp_bytes_to_key(password, self.spec.key_size)

        # Our own salt goes out in front of the first byte we send. It is
        # generated here rather than passed in so a caller cannot
        # accidentally reuse one; `salt` is injectable only so a test can
        # pin a known-answer vector.
        self.write_salt = salt if salt is not None else os.urandom(self.spec.salt_size)
        if len(self.write_salt) != self.spec.salt_size:
            raise ValueError(f"salt must be {self.spec.salt_size} bytes")

        self._write_aead = self.spec.aead(
            derive_subkey(self.master_key, self.write_salt, self.spec.key_size)
        )
        self._write_nonce = NonceCounter()

        self._read_aead = None
        self._read_nonce = None
        self.read_salt = None
        self._salt_sent = False

    # -- shapes the caller needs to read exact byte counts ---------------

    @property
    def salt_size(self):
        return self.spec.salt_size

    @property
    def tag_size(self):
        return self.spec.tag_size

    @property
    def length_block_size(self):
        """Bytes to read for an encrypted length field, tag included."""
        return 2 + self.spec.tag_size

    # -- writing ---------------------------------------------------------

    @staticmethod
    def request(host, port):
        """The first plaintext a client sends: where to connect."""
        return socks5_address(host, port)

    def seal(self, plaintext: bytes) -> bytes:
        """Frame and encrypt `plaintext`, prefixing the salt on first use.

        Splits at `MAX_PAYLOAD` rather than refusing a long payload: the
        chunk size is a wire constraint, and making every caller aware of
        it is how a 20 KB request becomes a bug report.
        """
        out = bytearray()
        if not self._salt_sent:
            out.extend(self.write_salt)
            self._salt_sent = True

        view = memoryview(plaintext)
        # A zero-length payload still produces a chunk. A Shadowsocks
        # chunk of length 0 is not meaningful, so this is the one case
        # that is refused rather than silently dropped -- a caller sealing
        # nothing has a bug, and sending nothing would hide it.
        if not view:
            raise ValueError("refusing to seal an empty payload")

        while view:
            piece = view[:MAX_PAYLOAD]
            view = view[MAX_PAYLOAD:]
            out.extend(
                self._write_aead.encrypt(
                    self._write_nonce.next(), struct.pack("!H", len(piece)), None
                )
            )
            out.extend(
                self._write_aead.encrypt(self._write_nonce.next(), bytes(piece), None)
            )
        return bytes(out)

    # -- reading ---------------------------------------------------------

    def begin_read(self, salt: bytes) -> None:
        """Take the server's salt and derive the read subkey from it.

        The two directions are independent: the server picks its own salt
        and its own counter starts at zero again. Sharing either would
        reuse a nonce under a key, which is the one failure AEAD does not
        survive.
        """
        if len(salt) != self.spec.salt_size:
            raise ShadowsocksError(
                f"server salt is {len(salt)} bytes, expected "
                f"{self.spec.salt_size} for {self.spec.name}"
            )
        self.read_salt = bytes(salt)
        self._read_aead = self.spec.aead(
            derive_subkey(self.master_key, self.read_salt, self.spec.key_size)
        )
        self._read_nonce = NonceCounter()

    def open_length(self, block: bytes) -> int:
        """Decrypt a length field. Returns the payload byte count."""
        plaintext = self._open(block, self.length_block_size, "length")
        (length,) = struct.unpack("!H", plaintext)
        if length == 0 or length > MAX_PAYLOAD:
            # A server that sends this is not a Shadowsocks server, or we
            # have lost framing sync. Either way continuing would read a
            # wrong number of bytes and misreport everything after it.
            raise ShadowsocksError(
                f"chunk length {length} is outside 1..{MAX_PAYLOAD}"
            )
        return length

    def open_payload(self, block: bytes, length: int) -> bytes:
        return self._open(block, length + self.spec.tag_size, "payload")

    def _open(self, block, expected, what):
        if self._read_aead is None:
            raise ShadowsocksError("begin_read() has not been called")
        if len(block) != expected:
            raise ShadowsocksError(
                f"{what} block is {len(block)} bytes, expected {expected}"
            )
        try:
            return self._read_aead.decrypt(self._read_nonce.next(), bytes(block), None)
        except InvalidTag:
            raise DecryptionFailed(
                f"the {what} block did not authenticate under our key"
            )
