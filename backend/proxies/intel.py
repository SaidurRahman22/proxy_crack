"""IP intelligence: which network an address sits on, and whose it is.

The chain-diversity feature (`proxies.diversity`) needs one fact per hop
that the inventory does not contain: the originating AS. This module gets
it, caches it, and is honest when it cannot.

It mirrors `proxies.probing` deliberately -- an abstract source, one real
implementation, one deterministic fake, and a dotted path in settings that
swaps them. That shape worked for R1 and the reasons are the same here: the
real source is a network service this project's tests must never touch.

Three implementations
---------------------
* `CymruDnsIpIntel` -- **the default.** Team Cymru's IP-to-ASN mapping,
  queried over DNS: a TXT record at `<reversed-ip>.origin.asn.cymru.com`
  gives the AS number, the announced prefix, the country and the registry;
  a second TXT at `AS<n>.asn.cymru.com` gives the AS name. It is free, it
  needs no account and no API key, and it is the service the rest of the
  industry already uses for this. It is also *someone else's free service*,
  which is why everything below is cached hard and rate limited.
* `IpApiIntel` -- an HTTP alternative (ip-api.com by default, also free and
  keyless). Second implementation behind the same interface, mostly to
  prove the interface is actually an interface. It reports **no announced
  prefix and no registry**, so two hops inside one operator's /19 but in
  different /24s read as unrelated. That is a real hole and the reason it
  is not the default.
* `FakeIpIntel` -- deterministic, no sockets. **Every test uses this one.**

No new dependency
-----------------
`CymruDnsIpIntel` speaks DNS over a UDP socket from the standard library
rather than pulling in a resolver package. The query is one TXT record with
no EDNS, no DNSSEC and no TCP fallback; `_dns_txt` is about eighty lines and
is tested against captured wire-format responses rather than the network.

**It will not use a public resolver by default.** The queries are a list of
the operator's proxy inventory, one host at a time, and handing that list to
Google or Cloudflare to save a configuration line would be a disclosure this
product exists to avoid. With no resolver configured and no `/etc/resolv.conf`
to read, the lookup fails with a message naming the setting. That is
deliberately more annoying than a default, and the annoyance is the point.

Caching, and why it is not optional
-----------------------------------
An ASN assignment changes on the order of years. A lookup per probe, per
chain render, or per API call would be wasteful, slow, and rude to a free
service -- and would make the diversity endpoint's latency depend on a
third party's DNS. So every answer is written to `IpIntelRecord` with an
explicit `looked_up_at` and `expires_at`, failures included (on a much
shorter TTL, so a transient outage does not pin a host to "unknown" for a
month).

The read path used by the mobile API never performs a lookup at all: it
reads the cache and reports anything missing as unknown. A client must not
be able to drive outbound DNS by opening a screen.
"""

import abc
import ipaddress
import json
import logging
import os
import random
import re
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, Iterable, List, Optional, Sequence

from django.conf import settings
from django.utils import timezone
from django.utils.module_loading import import_string

from proxies.models import IpIntelRecord, IpIntelStatus

logger = logging.getLogger("proxycrack.intel")

__all__ = [
    "CymruDnsIpIntel",
    "FakeIpIntel",
    "IntelBatch",
    "IpApiIntel",
    "IpFacts",
    "IpIntel",
    "IpIntelError",
    "IpIntelUnavailable",
    "RateLimiter",
    "cached_facts",
    "get_ip_intel",
    "intel_ttl",
    "lookup_hosts",
    "normalise_host",
    "store_facts",
]

DEFAULT_TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IpFacts:
    """One answer about one host. Immutable, database-free.

    `status` is never inferred from the other fields. A source that answers
    "I have no origin for 10.0.0.1" and a source that times out both leave
    `asn` null, and treating those as the same thing would mean a network
    outage looked like a private address.
    """

    query: str
    ip: Optional[str] = None
    asn: Optional[int] = None
    asn_org: str = ""
    prefix: str = ""
    country: str = ""
    registry: str = ""
    status: str = IpIntelStatus.OK
    detail: str = ""
    source: str = ""

    @classmethod
    def unknown(cls, query, *, detail="", source="", status=IpIntelStatus.ERROR, ip=None):
        return cls(
            query=query, ip=ip, status=status, detail=str(detail)[:255], source=source
        )

    @property
    def is_known(self):
        return self.status == IpIntelStatus.OK and self.asn is not None


class IpIntelError(Exception):
    """A lookup that did not produce an answer. Recorded, never raised past
    the batch runner."""


class IpIntelUnavailable(IpIntelError):
    """The source cannot be used at all in this environment.

    Distinct from a failed lookup: no resolver configured, or an
    implementation that was never given what it needs. Retrying every host
    in the inventory against it is pointless, so the batch runner stops
    rather than writing ten thousand identical error rows.
    """


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class RateLimiter:
    """Minimum interval between calls, shared across threads.

    Clock and sleep are injectable so the limiter can be tested without a
    test that takes a real minute to run -- the alternative is either an
    untested limiter or a slow suite, and both are how rate limiting ends
    up disabled.
    """

    def __init__(self, per_minute, *, clock=time.monotonic, sleeper=time.sleep):
        self.per_minute = max(0.0, float(per_minute or 0.0))
        self.min_interval = 60.0 / self.per_minute if self.per_minute > 0 else 0.0
        self._clock = clock
        self._sleep = sleeper
        self._lock = threading.Lock()
        self._next_at = None

    def wait(self):
        """Block until the next call is allowed. Returns seconds slept."""
        if self.min_interval <= 0:
            return 0.0
        with self._lock:
            now = self._clock()
            if self._next_at is None or now >= self._next_at:
                self._next_at = now + self.min_interval
                return 0.0
            delay = self._next_at - now
            self._next_at += self.min_interval
        self._sleep(delay)
        return delay


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class IpIntel(abc.ABC):
    """Answers "what AS is this host on" for one host at a time.

    One host per call rather than a batch because the default source is a
    DNS query per address and there is nothing to batch; an implementation
    that *can* batch overrides `lookup_many`.
    """

    #: Source label written to `IpIntelRecord.source`.
    name = "ipintel"

    #: Whether the batch runner should apply the configured rate limit to
    #: this implementation. False for the fake, which touches nothing and
    #: must never make a test sleep.
    rate_limited = True

    @abc.abstractmethod
    def lookup(self, host: str, *, timeout: float = DEFAULT_TIMEOUT) -> IpFacts:
        """Must not raise for a lookup failure -- return `IpFacts` with a
        non-OK status instead. May raise `IpIntelUnavailable` when the
        source cannot be used at all."""

    def lookup_many(self, hosts, *, timeout=DEFAULT_TIMEOUT, limiter=None):
        """Serial by default, with the rate limit applied between calls."""
        for host in hosts:
            if limiter is not None and self.rate_limited:
                limiter.wait()
            yield self.lookup(host, timeout=timeout)


# ---------------------------------------------------------------------------
# A very small DNS client
# ---------------------------------------------------------------------------


class DnsError(IpIntelError):
    pass


class DnsNameError(DnsError):
    """NXDOMAIN. For an origin lookup this is a real answer: nobody
    announces a route covering this address."""


def _encode_name(name):
    out = bytearray()
    for label in name.rstrip(".").split("."):
        encoded = label.encode("ascii")
        if not 1 <= len(encoded) <= 63:
            raise DnsError(f"bad DNS label {label!r}")
        out.append(len(encoded))
        out += encoded
    out.append(0)
    return bytes(out)


def _skip_name(data, offset):
    """Step over a (possibly compressed) name, returning the next offset."""
    while True:
        if offset >= len(data):
            raise DnsError("truncated name")
        length = data[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:
            # A pointer is always the last thing in a name.
            return offset + 2
        offset += 1 + length


def _parse_txt(data):
    """TXT strings from a response. Raises on anything unexpected.

    The question section is stepped over rather than compared against the
    name we asked for: the transaction id is already checked by the
    caller, and a resolver that answers a different question is a broken
    resolver, not an attack this UDP client could defend against anyway.
    """
    if len(data) < 12:
        raise DnsError("short response")
    _id, flags, qdcount, ancount = struct.unpack(">HHHH", data[:8])
    if flags & 0x0200:
        # Truncated. The answers we ask for fit in a datagram; a TC bit
        # means something is wrong, and silently using a partial answer
        # would under-report the hops that share a network.
        raise DnsError("response truncated (TC set)")
    rcode = flags & 0x000F
    if rcode == 3:
        raise DnsNameError("NXDOMAIN")
    if rcode != 0:
        raise DnsError(f"DNS rcode {rcode}")

    offset = 12
    for _ in range(qdcount):
        offset = _skip_name(data, offset) + 4

    strings = []
    for _ in range(ancount):
        offset = _skip_name(data, offset)
        if offset + 10 > len(data):
            raise DnsError("truncated answer header")
        rtype, _rclass, _ttl, rdlength = struct.unpack(">HHIH", data[offset : offset + 10])
        offset += 10
        rdata = data[offset : offset + rdlength]
        offset += rdlength
        if rtype != 16:  # not TXT (a CNAME in the chain, say)
            continue
        pos = 0
        parts = []
        while pos < len(rdata):
            length = rdata[pos]
            parts.append(rdata[pos + 1 : pos + 1 + length].decode("utf-8", "replace"))
            pos += 1 + length
        strings.append("".join(parts))
    return strings


def _dns_txt(name, resolvers, timeout):
    """TXT strings for `name`, trying each resolver in turn.

    Deliberately minimal: UDP only, no EDNS, no DNSSEC, no TCP fallback.
    The records this module asks for are a few dozen bytes.
    """
    if not resolvers:
        raise IpIntelUnavailable(
            "No DNS resolver configured. Set IP_INTEL_DNS_RESOLVERS to the "
            "resolver this host should use. There is no public default on "
            "purpose: these queries are a list of your proxy inventory, "
            "and sending it to a third-party resolver to save a setting "
            "would be exactly the disclosure this product exists to avoid."
        )

    query = _encode_name(name) + struct.pack(">HH", 16, 1)
    last_error = None
    for resolver in resolvers:
        request_id = random.randrange(0, 65536)
        packet = struct.pack(">HHHHHH", request_id, 0x0100, 1, 0, 0, 0) + query
        family = socket.AF_INET6 if ":" in resolver else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_DGRAM)
        try:
            sock.settimeout(timeout)
            sock.sendto(packet, (resolver, 53))
            while True:
                data, _addr = sock.recvfrom(4096)
                if len(data) >= 2 and struct.unpack(">H", data[:2])[0] == request_id:
                    break
            return _parse_txt(data)
        except DnsNameError:
            raise
        except (OSError, DnsError) as exc:
            last_error = exc
            continue
        finally:
            sock.close()
    raise DnsError(str(last_error) if last_error else "no resolver answered")


def configured_resolvers():
    """Resolver addresses to query, most explicit first.

    `settings.IP_INTEL_DNS_RESOLVERS` wins. Failing that, `/etc/resolv.conf`
    -- the host's own resolver, which is the right default where it exists
    because it is already the resolver everything else on the box uses.
    Windows has no equivalent file and no stdlib accessor, so there the
    setting is required.
    """
    configured = getattr(settings, "IP_INTEL_DNS_RESOLVERS", None) or []
    if configured:
        return [str(entry).strip() for entry in configured if str(entry).strip()]

    path = "/etc/resolv.conf"
    if os.path.exists(path):
        found = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if line.startswith("nameserver"):
                        parts = line.split()
                        if len(parts) >= 2:
                            found.append(parts[1])
        except OSError:
            return []
        return found
    return []


# ---------------------------------------------------------------------------
# Host handling
# ---------------------------------------------------------------------------


def normalise_host(host):
    """The cache key for a host. Lower-cased, trimmed, brackets stripped.

    `[2001:db8::1]` and `2001:db8::1` are one host; so are `Proxy.Example.`
    and `proxy.example`. Two cache rows for one host would mean two lookups
    and, worse, two different answers in the same analysis.
    """
    text = (host or "").strip().strip(".").lower()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return text


def parse_ip(host):
    """`ipaddress` object if `host` is a literal address, else `None`."""
    try:
        return ipaddress.ip_address(normalise_host(host))
    except ValueError:
        return None


def resolve_host(host, *, timeout=DEFAULT_TIMEOUT):
    """A literal address for `host`, resolving a name if necessary.

    Raises `IpIntelError` rather than returning None so the caller records
    *why* a host is unknown. `socket.getaddrinfo` has no timeout parameter;
    the default resolver's own timeout applies, which is why name-based
    hosts are slower to refresh than literals and why the cache matters.
    """
    literal = parse_ip(host)
    if literal is not None:
        return str(literal)
    try:
        info = socket.getaddrinfo(
            normalise_host(host), None, proto=socket.IPPROTO_TCP
        )
    except OSError as exc:
        raise IpIntelError(f"name resolution failed: {exc}") from exc
    for entry in info:
        address = entry[4][0]
        parsed = parse_ip(address)
        if parsed is not None:
            return str(parsed)
    raise IpIntelError("name resolved to no usable address")


# ---------------------------------------------------------------------------
# Team Cymru over DNS -- the default
# ---------------------------------------------------------------------------


class CymruDnsIpIntel(IpIntel):
    """Team Cymru's IP-to-ASN mapping, over DNS TXT.

    Two queries per address, the second usually served from a per-instance
    memo because one AS covers many addresses:

        1.2.3.4   ->  4.3.2.1.origin.asn.cymru.com    TXT
                      "15169 | 1.2.3.0/24 | US | arin | 2007-03-13"
        AS15169   ->  AS15169.asn.cymru.com           TXT
                      "15169 | US | arin | 2000-03-30 | GOOGLE, US"

    IPv6 uses `origin6.asn.cymru.com` with the address expanded to nibbles.

    The AS-name query is best effort. A missing name leaves `asn_org`
    blank; it does not invalidate an answer whose AS number, prefix and
    country are already in hand, because the ASN is the field the
    correlation checks actually depend on.
    """

    name = "cymru-dns"

    ORIGIN_ZONE = "origin.asn.cymru.com"
    ORIGIN6_ZONE = "origin6.asn.cymru.com"
    AS_ZONE = "asn.cymru.com"

    def __init__(self, resolvers=None):
        self._resolvers = resolvers
        self._as_names: Dict[int, str] = {}
        self._lock = threading.Lock()

    def resolvers(self):
        return self._resolvers if self._resolvers is not None else configured_resolvers()

    def query_name(self, address):
        parsed = ipaddress.ip_address(address)
        if parsed.version == 4:
            reversed_octets = ".".join(reversed(str(parsed).split(".")))
            return f"{reversed_octets}.{self.ORIGIN_ZONE}"
        nibbles = ".".join(reversed(parsed.exploded.replace(":", "")))
        return f"{nibbles}.{self.ORIGIN6_ZONE}"

    def lookup(self, host, *, timeout=DEFAULT_TIMEOUT):
        query = normalise_host(host)
        resolvers = self.resolvers()

        try:
            address = resolve_host(query, timeout=timeout)
        except IpIntelError as exc:
            return IpFacts.unknown(query, detail=str(exc), source=self.name)

        try:
            answers = _dns_txt(self.query_name(address), resolvers, timeout)
        except DnsNameError:
            # A real answer: no route covering this address is announced.
            # Private ranges, bogons and unrouted space all land here.
            return IpFacts.unknown(
                query,
                ip=address,
                status=IpIntelStatus.NOT_FOUND,
                detail="no announced origin for this address",
                source=self.name,
            )
        except IpIntelUnavailable:
            raise
        except IpIntelError as exc:
            return IpFacts.unknown(query, ip=address, detail=str(exc), source=self.name)

        parsed = self.parse_origin(answers)
        if parsed is None:
            return IpFacts.unknown(
                query,
                ip=address,
                detail="origin record did not parse",
                source=self.name,
            )

        asn, prefix, country, registry = parsed
        return IpFacts(
            query=query,
            ip=address,
            asn=asn,
            asn_org=self.as_name(asn, resolvers, timeout),
            prefix=prefix,
            country=country,
            registry=registry,
            status=IpIntelStatus.OK,
            source=self.name,
        )

    @staticmethod
    def parse_origin(answers):
        """`(asn, prefix, country, registry)` from the origin TXT records.

        An address covered by more than one announcement gets more than one
        record. The most specific prefix wins -- that is the route traffic
        actually follows, and it is also the tighter correlation claim.
        """
        best = None
        for answer in answers or []:
            fields = [part.strip() for part in answer.split("|")]
            if len(fields) < 2:
                continue
            # The first field can list several ASNs for a multi-origin
            # prefix ("13335 209242"). Take the first; the alternative is
            # inventing a representation for a rare case.
            asn_text = fields[0].split()[0] if fields[0].split() else ""
            if not asn_text.isdigit():
                continue
            prefix = fields[1]
            country = fields[2].upper() if len(fields) > 2 else ""
            registry = fields[3].lower() if len(fields) > 3 else ""
            try:
                length = ipaddress.ip_network(prefix, strict=False).prefixlen
            except ValueError:
                length = -1
            candidate = (length, (int(asn_text), prefix, country, registry))
            if best is None or candidate[0] > best[0]:
                best = candidate
        return best[1] if best else None

    def as_name(self, asn, resolvers, timeout):
        with self._lock:
            if asn in self._as_names:
                return self._as_names[asn]
        try:
            answers = _dns_txt(f"AS{asn}.{self.AS_ZONE}", resolvers, timeout)
        except IpIntelError:
            answers = []
        name = ""
        for answer in answers:
            fields = [part.strip() for part in answer.split("|")]
            if len(fields) >= 5 and fields[4]:
                name = fields[4][:200]
                break
        with self._lock:
            self._as_names[asn] = name
        return name


# ---------------------------------------------------------------------------
# HTTP alternative
# ---------------------------------------------------------------------------


class IpApiIntel(IpIntel):
    """ip-api.com over HTTP. Free, keyless, and less useful.

    Here to keep `IpIntel` an actual interface rather than a decoration on
    one implementation, and as an escape hatch where outbound DNS to port
    53 is blocked but HTTP is not.

    **It reports no announced prefix and no registry.** The same-network
    check still works, because `proxies.diversity` derives a /24
    neighbourhood from the address itself -- but it loses the case the
    announced prefix is actually for: two hops inside one operator's /19
    that happen to sit in different /24s read as unrelated. That is a real
    hole in the most-specific direction and the reason this is not the
    default.
    """

    name = "ip-api"

    DEFAULT_URL = "http://ip-api.com/json/{ip}?fields=status,message,countryCode,as,asname,query"
    _AS_RE = re.compile(r"^AS(\d+)\s*(.*)$", re.IGNORECASE)

    def url_template(self):
        return getattr(settings, "IP_INTEL_HTTP_URL", self.DEFAULT_URL)

    def lookup(self, host, *, timeout=DEFAULT_TIMEOUT):
        query = normalise_host(host)
        try:
            address = resolve_host(query, timeout=timeout)
        except IpIntelError as exc:
            return IpFacts.unknown(query, detail=str(exc), source=self.name)

        url = self.url_template().format(ip=address)
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "proxycrack-ipintel/1.0"}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return IpFacts.unknown(query, ip=address, detail=str(exc)[:200], source=self.name)

        return self.parse(query, address, payload)

    @classmethod
    def parse(cls, query, address, payload):
        if not isinstance(payload, dict):
            return IpFacts.unknown(
                query, ip=address, detail="unexpected payload", source=cls.name
            )
        if payload.get("status") != "success":
            return IpFacts.unknown(
                query,
                ip=address,
                status=IpIntelStatus.NOT_FOUND,
                detail=str(payload.get("message") or "lookup unsuccessful")[:200],
                source=cls.name,
            )
        match = cls._AS_RE.match(str(payload.get("as") or "").strip())
        if not match:
            return IpFacts.unknown(
                query,
                ip=address,
                status=IpIntelStatus.NOT_FOUND,
                detail="no AS in response",
                source=cls.name,
            )
        return IpFacts(
            query=query,
            ip=address,
            asn=int(match.group(1)),
            asn_org=(payload.get("asname") or match.group(2) or "")[:200],
            # No prefix, no registry. Stated as absent rather than guessed
            # at -- a /24 invented from an address is a correlation claim
            # about infrastructure nobody measured.
            prefix="",
            country=str(payload.get("countryCode") or "").upper()[:8],
            registry="",
            status=IpIntelStatus.OK,
            source=cls.name,
        )


# ---------------------------------------------------------------------------
# The fake
# ---------------------------------------------------------------------------


class FakeIpIntel(IpIntel):
    """Deterministic intel. No sockets, no clock, no network.

    Scripted per host:

        FakeIpIntel({
            "1.1.1.1": IpFacts(query="1.1.1.1", asn=13335, ...),
            "2.2.2.2": [first, second],          # in order, last repeats
            "3.3.3.3": lambda host: ...,          # computed
        }, default=...)

    `calls` records every host asked for, in order, which is how a test
    proves the cache actually prevented a second lookup.

    The default is an *unknown* answer rather than a plausible invented
    one. A fake that silently makes up an ASN for an unscripted host would
    let a test assert diversity that the code never computed.
    """

    name = "fake"
    rate_limited = False

    def __init__(self, results=None, default=None, unavailable=False):
        self.results = {normalise_host(k): v for k, v in dict(results or {}).items()}
        self.default = default
        self.unavailable = unavailable
        self.calls: List[str] = []
        self._lock = threading.Lock()

    def lookup(self, host, *, timeout=DEFAULT_TIMEOUT):
        query = normalise_host(host)
        with self._lock:
            self.calls.append(query)
            if self.unavailable:
                raise IpIntelUnavailable("fake: source unavailable")
            scripted = self.results.get(query, self.default)
            if isinstance(scripted, list):
                if not scripted:
                    scripted = self.default
                else:
                    scripted = scripted.pop(0) if len(scripted) > 1 else scripted[0]
        if callable(scripted):
            scripted = scripted(query)
        if scripted is None:
            return IpFacts.unknown(
                query,
                status=IpIntelStatus.NOT_FOUND,
                detail="fake: no scripted answer",
                source=self.name,
            )
        if scripted.query != query:
            # Convenience: a test may script facts without repeating the
            # host. Keeping the key authoritative stops a typo in the
            # fixture from writing a cache row under the wrong host.
            return IpFacts(**{**scripted.__dict__, "query": query})
        return scripted


def get_ip_intel() -> IpIntel:
    """Instantiate the configured source.

    A dotted path for the same reason `PROXY_PROBER` is one: a test swaps
    in `FakeIpIntel` with `override_settings` and nothing has to thread an
    argument through a command, a view and a batch runner.
    """
    path = getattr(settings, "IP_INTEL_BACKEND", "proxies.intel.CymruDnsIpIntel")
    return import_string(path)()


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def intel_ttl(status=IpIntelStatus.OK):
    """How long an answer of this kind is trusted.

    A success lasts a month: ASN assignments move on the order of years,
    and a month of staleness is a far smaller error than the alternative of
    querying a free service on every page render.

    A failure lasts hours. A negative answer that lasted a month would mean
    one DNS outage pinned a host to "unknown" until next quarter, and
    "unknown" is the state this feature is trying to get *out* of.
    """
    if status == IpIntelStatus.OK:
        days = float(getattr(settings, "IP_INTEL_TTL_DAYS", 30))
        return timedelta(days=max(0.0, days))
    hours = float(getattr(settings, "IP_INTEL_NEGATIVE_TTL_HOURS", 6))
    return timedelta(hours=max(0.0, hours))


def store_facts(facts: IpFacts, *, now=None) -> IpIntelRecord:
    """Write one answer to the cache, replacing any previous one."""
    now = now or timezone.now()
    record, _created = IpIntelRecord.objects.update_or_create(
        query=normalise_host(facts.query),
        defaults={
            "ip": facts.ip or None,
            "asn": facts.asn,
            "asn_org": (facts.asn_org or "")[:200],
            "prefix": (facts.prefix or "")[:64],
            "country": (facts.country or "")[:8],
            "registry": (facts.registry or "")[:32],
            "status": facts.status,
            "source": (facts.source or "")[:64],
            "detail": (facts.detail or "")[:255],
            "looked_up_at": now,
            "expires_at": now + intel_ttl(facts.status),
        },
    )
    return record


def cached_facts(hosts: Iterable[str], *, now=None, include_stale=False):
    """`{host_key: IpIntelRecord}` for hosts already in the cache.

    **Performs no lookups.** This is the read the mobile API uses: a client
    opening a chain screen must not be able to make this server emit DNS.

    Stale rows are omitted by default. `include_stale=True` is for the
    refresh path, which wants to know what it is about to replace.
    """
    now = now or timezone.now()
    keys = [normalise_host(host) for host in hosts]
    keys = [key for key in keys if key]
    if not keys:
        return {}
    rows = IpIntelRecord.objects.filter(query__in=set(keys))
    if not include_stale:
        rows = rows.filter(expires_at__gt=now)
    return {row.query: row for row in rows}


@dataclass
class IntelBatch:
    """What one refresh run did."""

    records: Dict[str, IpIntelRecord] = field(default_factory=dict)
    looked_up: List[str] = field(default_factory=list)
    from_cache: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    unavailable: str = ""

    @property
    def known(self):
        return sum(1 for record in self.records.values() if record.is_known)


def lookup_hosts(
    hosts: Sequence[str],
    *,
    intel: Optional[IpIntel] = None,
    force: bool = False,
    now=None,
    timeout: Optional[float] = None,
    limiter: Optional[RateLimiter] = None,
) -> IntelBatch:
    """Fill the cache for `hosts`, looking up only what is missing or stale.

    Serial, not concurrent. Probing is parallel because it is our own
    bandwidth against our own proxies; this is a free third-party service,
    and eight threads hammering it is how a project gets blocked. The rate
    limit is the deliberate brake and `IP_INTEL_RATE_LIMIT_PER_MINUTE`
    configures it.

    `IpIntelUnavailable` stops the whole run rather than being recorded per
    host. A missing resolver is not a fact about ten thousand addresses.
    """
    now = now or timezone.now()
    intel = intel or get_ip_intel()
    timeout = float(
        timeout
        if timeout is not None
        else getattr(settings, "IP_INTEL_TIMEOUT_SECONDS", DEFAULT_TIMEOUT)
    )
    limiter = limiter or RateLimiter(
        getattr(settings, "IP_INTEL_RATE_LIMIT_PER_MINUTE", 60)
    )

    batch = IntelBatch()
    ordered = []
    seen = set()
    for host in hosts:
        key = normalise_host(host)
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    if not ordered:
        return batch

    existing = {} if force else cached_facts(ordered, now=now)
    batch.records.update(existing)
    batch.from_cache.extend(sorted(existing))

    for key in ordered:
        if key in existing:
            continue
        if intel.rate_limited:
            limiter.wait()
        try:
            facts = intel.lookup(key, timeout=timeout)
        except IpIntelUnavailable as exc:
            batch.unavailable = str(exc)
            logger.warning("ip_intel_unavailable source=%s detail=%s", intel.name, exc)
            break
        except Exception as exc:  # noqa: BLE001 -- our bug, not the host's
            logger.exception("ip intel source raised for %s", key)
            facts = IpFacts.unknown(
                key, detail=f"{type(exc).__name__}: {exc}", source=intel.name
            )
        record = store_facts(facts, now=now)
        batch.records[key] = record
        batch.looked_up.append(key)
        if not record.is_known:
            batch.failed.append(key)

    return batch
