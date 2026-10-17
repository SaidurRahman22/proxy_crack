"""Provider sync: ask the vendor what we actually bought, and reconcile.

Until this existed, proxies entered the database one at a time, by hand or
by CSV, and nothing ever talked to the provider. Three things follow from
that, and all three are worse than they sound:

* **You load a fraction of what you pay for.** The account this was built
  against rents ten endpoints and the database held one.
* **Rotation decays the inventory with no way back up.** Providers replace
  addresses. The health checker correctly marks the dead one dead; the
  replacement never arrives, because nothing is listening for it.
* **Nobody can say what a run did.** An unattended job that mutates the
  sellable asset needs the record an import has.

Shape
-----
The same three-part shape as `proxies.probing` and `proxies.intel`, for the
same reason -- all three talk to a third party that CI must never reach:

* `ProviderSync`      the interface
* `WebshareSync`      the real adapter, over plain `urllib`
* `FakeProviderSync`  deterministic, no sockets. **Every test uses this
                      one**, except the adapter's own tests, which drive
                      `WebshareSync` through an injected opener against
                      captured response bodies.

`settings.PROVIDER_SYNC_BACKEND` is the dotted path that selects one, and
`get_provider_sync()` instantiates it.

THE API TOKEN
-------------
The token is as sensitive as the proxies it buys: anyone holding it can
list every credential on the account, in plaintext, in one request. It is
therefore handled on the same terms as `PROXY_CRED_KEY`:

* read from the environment, never from a committed file;
* **never** written to a log line, an exception message, an
  `AdminAuditLog` payload, a `ProviderSyncRun.errors` row, or command
  output;
* absent means a loud, specific failure naming the variable, never a
  silent no-op that leaves the operator believing a sync ran.

`_redact` is the backstop: every message this module constructs passes
through it, so a token that reaches a string by a route nobody anticipated
is still removed before the string escapes. The rule is "do not put it in
the message"; the backstop is for when the rule is broken.

The response bodies are the other half of the problem. They contain the
account's proxy passwords in plaintext, so **no response body is ever
quoted into an error, a log, or a stored row** -- only the status code and
a description of the shape that was wrong.

ABSENT IS NOT EMPTY -- AND HERE IT IS STRICTER THAN THE CSV
-----------------------------------------------------------
`proxies.importers` learned this the hard way: a metadata-only CSV once
blanked the credentials of every endpoint it touched, irrecoverably,
because `update_or_create` wrote every field whether or not the file
carried it. The rule there is that an **absent column** is not data and is
left alone, while a **present but empty cell** is a deliberate clear.

This module applies the first half and deliberately drops the second. A
credential is written only when the provider states a **non-empty** value;
an absent key and an empty string are both treated as "not stated" and
leave the stored credential untouched.

The divergence is not an oversight. A CSV is authored by a human who can
mean "clear this"; an empty string in an API response at 4am means a
partial page, a truncated body, or a vendor-side bug, and there is nobody
watching. A sync can therefore never destroy a credential -- clearing one
remains a deliberate act through the admin console.

WHAT A SYNC MAY AND MAY NOT DO
------------------------------
* It **never deletes**. A vanished endpoint is disabled. Deleting it would
  take its `ProxyProbe` history with it and would break any saved chain
  that references it, which `ChainHop.proxy`'s `PROTECT` turns into a
  500 rather than a silent loss -- but losing the history is the real cost.
* It **only undoes its own decisions**. A proxy an operator switched off
  stays off even when the provider lists it; only `disabled_by_sync` rows
  are re-enabled. See `Proxy.disabled_by_sync`.
* It **never concludes "vanished" from a partial listing.** If any page
  failed, the run is `PARTIAL` and disables nothing. Half a listing looks
  exactly like half the inventory disappearing, and the one thing worse
  than an inventory that decays silently is one that disables itself in a
  single run because a vendor's API returned a 502 on page two.
"""

import abc
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from proxies.intel import (
    INTEL_WRITTEN,
    IpFacts,
    normalise_host,
    provider_source,
    store_provider_facts,
)
from proxies.models import (
    IpIntelStatus,
    ProviderSyncRun,
    ProviderSyncStatus,
    Proxy,
    ProxyProtocol,
    ProxyTier,
    provider_key,
)

logger = logging.getLogger("proxycrack.providers")

__all__ = [
    "FakeProviderSync",
    "ProviderCredentialsMissing",
    "ProviderListing",
    "ProviderProxy",
    "ProviderSync",
    "ProviderSyncError",
    "ProviderUnavailable",
    "RecordOutcome",
    "SyncAction",
    "SyncOutcome",
    "WebshareSync",
    "get_provider_sync",
    "sync_provider",
]

DEFAULT_TIMEOUT = 20.0
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_RECORDS = 10000

#: Hard ceiling on pages, independent of the record cap. A provider whose
#: `next` link points at itself would otherwise be an infinite loop inside
#: a management command.
MAX_PAGES = 500

#: Stored `errors` are rendered in the admin SPA and may be exported.
MAX_STORED_ERRORS = 200


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ProviderSyncError(Exception):
    """Anything that stopped a sync. Messages here are redacted by
    construction -- see `_redact`."""


class ProviderCredentialsMissing(ProviderSyncError):
    """No API token is configured.

    Deliberately fatal rather than a skipped run. A sync that quietly does
    nothing because a variable is unset is indistinguishable, from the
    outside, from a sync that ran and found nothing to do -- and the second
    reading is the one an operator will reach for, because it is the one
    that requires no action.
    """


class ProviderUnavailable(ProviderSyncError):
    """The provider returned nothing usable at all.

    Distinct from a partial listing: it means the run has no information,
    not that it has incomplete information.
    """


class ProviderRecordError(ProviderSyncError):
    """One record in the listing is unusable. The rest of the run continues."""


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderProxy:
    """One endpoint as the provider describes it. Immutable, database-free.

    `username`, `password`, `country` and `city` are `Optional[str]` and
    `None` means **the provider did not state this**. Nothing downstream
    may turn a `None` into a write; see the module docstring.

    `asn` / `asn_org` are what makes this worth more than a proxy list:
    they feed `IpIntelRecord` and therefore R2's diversity scoring, on a
    host where no registry lookup is possible.
    """

    external_id: str
    host: str
    port: int
    protocol: str = ProxyProtocol.HTTP
    username: Optional[str] = None
    password: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    asn: Optional[int] = None
    asn_org: str = ""
    #: The provider's own verdict on this endpoint. Recorded, never acted
    #: on -- see `sync_provider`.
    valid: bool = True
    #: Webshare's `high_country_confidence`. False means the geolocation
    #: is the vendor's guess rather than something it stands behind.
    country_confident: bool = True

    @property
    def endpoint(self):
        return f"{self.host}:{self.port}"

    def intel_facts(self, source_name):
        """The provider's ASN claim, as `IpFacts`, or None if it made none.

        `prefix` and `registry` are left empty rather than invented. A /24
        derived from an address is a claim about infrastructure nobody
        measured, and `proxies.diversity` already derives its own
        neighbourhood block when no announced prefix is known.
        """
        if self.asn is None:
            return None
        return IpFacts(
            query=normalise_host(self.host),
            ip=self.host,
            asn=self.asn,
            asn_org=(self.asn_org or "")[:200],
            prefix="",
            country=(self.country or "").upper()[:8],
            registry="",
            status=IpIntelStatus.OK,
            source=provider_source(source_name),
        )


@dataclass
class ProviderListing:
    """Everything one fetch obtained, and how confident it is in it.

    `complete` is the load-bearing field. It is True only when the whole
    listing was retrieved: every page fetched, no cap reached, no page
    error. Only a complete listing supports the conclusion that an endpoint
    which is not in it has vanished.
    """

    records: List[ProviderProxy] = field(default_factory=list)
    pages: int = 0
    complete: bool = False
    truncated: bool = False
    reported_total: Optional[int] = None
    #: Redacted, body-free descriptions of records and pages that failed.
    errors: List[str] = field(default_factory=list)

    @property
    def external_ids(self):
        return {record.external_id for record in self.records}


class SyncAction:
    """What happened to one endpoint. Exactly one per record.

    Ordered by how much an operator needs to see it, which is also the
    precedence when more than one applies: a row that was both adopted and
    had its country filled in reports as `adopted`, because that is the
    event that happens once in the row's life.
    """

    FAILED = "failed"
    CREATED = "created"
    ADOPTED = "adopted"
    REENABLED = "reenabled"
    UPDATED = "updated"
    DISABLED = "disabled"
    UNCHANGED = "unchanged"


@dataclass
class RecordOutcome:
    """One line of a sync's per-record report.

    Carries no credential: `fields` names the columns that changed and
    never their values, which is the same rule `adminapi.audit` applies --
    audit the access, never the value.
    """

    action: str
    external_id: str
    endpoint: str
    proxy_id: Optional[int] = None
    fields: Sequence[str] = ()
    detail: str = ""

    def __str__(self):
        line = f"{self.action:<10} {self.external_id:<12} {self.endpoint}"
        if self.fields:
            line += f"  [{', '.join(self.fields)}]"
        if self.detail:
            line += f"  -- {self.detail}"
        return line


@dataclass
class SyncOutcome:
    """What one call to `sync_provider` did, before and after persistence."""

    provider: str
    source: str
    dry_run: bool = False
    status: str = ProviderSyncStatus.OK
    listing: Optional[ProviderListing] = None
    outcomes: List[RecordOutcome] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    intel_written: int = 0
    intel_skipped: int = 0
    errors: List[str] = field(default_factory=list)
    run: Optional[ProviderSyncRun] = None

    def of(self, action):
        return [outcome for outcome in self.outcomes if outcome.action == action]

    def count(self, action):
        return self.counts.get(action, 0)

    @property
    def changed(self):
        return sum(
            self.counts.get(action, 0)
            for action in (
                SyncAction.CREATED,
                SyncAction.ADOPTED,
                SyncAction.UPDATED,
                SyncAction.REENABLED,
                SyncAction.DISABLED,
            )
        )

    def summary(self):
        return (
            f"{self.count(SyncAction.CREATED)} created, "
            f"{self.count(SyncAction.ADOPTED)} adopted, "
            f"{self.count(SyncAction.UPDATED)} updated, "
            f"{self.count(SyncAction.REENABLED)} re-enabled, "
            f"{self.count(SyncAction.DISABLED)} disabled, "
            f"{self.count(SyncAction.UNCHANGED)} unchanged, "
            f"{self.count(SyncAction.FAILED)} failed"
        )


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def _redact(text, *secrets):
    """Remove known secrets from a string that is about to escape.

    A backstop, not the control. The control is that nothing in this
    module puts a token or a password into a message in the first place.
    This exists because the cost of being wrong about that once -- a token
    in a log file, a stored error row, an audit payload -- is the entire
    account, and a four-line function is cheap insurance against a route
    nobody anticipated (an exception's `repr` of a request object, a
    urllib error carrying a URL somebody later added a query parameter to).
    """
    out = str(text)
    for secret in secrets:
        secret = str(secret or "")
        if len(secret) >= 8 and secret in out:
            out = out.replace(secret, "[redacted]")
    return out


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class ProviderSync(abc.ABC):
    """Lists the endpoints one provider account currently holds.

    One method, because that is the whole contract: everything about
    reconciling what comes back belongs to `sync_provider` and is
    deliberately not the adapter's business. An adapter that decided what
    to disable would have to be trusted with the inventory; this way it is
    trusted only with reading.
    """

    #: Written to `ProviderSyncRun.source` and, prefixed, to
    #: `IpIntelRecord.source`.
    name = "provider"

    #: Default value for `Proxy.provider` on rows this adapter manages.
    #: Instances may override it from settings so one adapter can serve two
    #: accounts under two labels.
    provider = "provider"

    @abc.abstractmethod
    def fetch(
        self,
        *,
        page_size: Optional[int] = None,
        max_records: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> ProviderListing:
        """Return everything the account currently holds.

        Must raise `ProviderCredentialsMissing` when it is not configured
        and `ProviderUnavailable` when it obtained nothing at all. A
        partially retrieved listing is **returned** with `complete=False`
        rather than raised: the new endpoints on the pages that did arrive
        are still worth creating, and the runner knows not to draw
        conclusions from the gaps.
        """


# ---------------------------------------------------------------------------
# Webshare
# ---------------------------------------------------------------------------


class WebshareSync(ProviderSync):
    """`GET https://proxy.webshare.io/api/v2/proxy/list/`.

    Verified against a live account. One page carries
    `{count, next, previous, results: [...]}` and each result carries
    exactly:

        id, username, password, proxy_address, port, valid,
        last_verification, country_code, city_name, created_at,
        asn_name, asn_number, high_country_confidence

    **There is no protocol field**, and this adapter does not invent one.
    Webshare's direct proxies speak HTTP on the returned port; SOCKS5,
    where a plan includes it, is a different port and is not described by
    this endpoint. Guessing per record would mean writing a protocol that
    decides how the engine dials the hop, from evidence that does not
    exist. `settings.WEBSHARE_PROTOCOL` states it once, for the account,
    where an operator can see and change it.

    `valid` is read and reported and **never acted on**. It is Webshare's
    answer to the same question `probe_proxies` answers from our own
    measurements, and R1 exists because we would rather measure than be
    told. Letting a vendor's flag disable inventory would also hand it a
    lever on what we can sell.

    The opener is injectable so the adapter's own tests drive real
    pagination, real header construction and real mapping against captured
    response bodies, without a socket. The network is the only part not
    covered, and it is the only part that is not ours.
    """

    name = "webshare"
    provider = "webshare"

    DEFAULT_URL = "https://proxy.webshare.io/api/v2/proxy/list/"
    DEFAULT_MODE = "direct"

    def __init__(self, *, token=None, url=None, mode=None, protocol=None, opener=None):
        self._token_override = token
        self.url = url or getattr(settings, "WEBSHARE_API_URL", self.DEFAULT_URL)
        self.mode = mode or getattr(settings, "WEBSHARE_MODE", self.DEFAULT_MODE)
        self.protocol = str(
            protocol or getattr(settings, "WEBSHARE_PROTOCOL", ProxyProtocol.HTTP)
        )
        self.provider = str(
            getattr(settings, "WEBSHARE_PROVIDER_LABEL", None) or self.provider
        )
        self._opener = opener or _urllib_opener

    # -- credentials ----------------------------------------------------

    def token(self):
        """The API token, or a loud failure naming the variable.

        Not cached on the instance and not read at construction: a sync
        object may be built by `get_provider_sync()` long before anything
        asks it to fetch, and a token read at import time is a token that
        survives in a process after the environment has been corrected.
        """
        token = self._token_override
        if token is None:
            token = getattr(settings, "WEBSHARE_API_TOKEN", "") or ""
        token = str(token).strip()
        if not token:
            raise ProviderCredentialsMissing(
                "WEBSHARE_API_TOKEN is not set, so the Webshare sync cannot "
                "run. Set it in backend/.env. Treat it exactly like "
                "PROXY_CRED_KEY: anyone holding it can list every proxy "
                "credential on the account in a single request, so it is "
                "never committed, never logged and never pasted into a "
                "ticket. There is no unauthenticated fallback on purpose -- "
                "a sync that quietly did nothing would look identical to a "
                "sync that found nothing to do."
            )
        return token

    # -- fetching -------------------------------------------------------

    def first_url(self, page_size):
        query = urllib.parse.urlencode(
            {"mode": self.mode, "page": 1, "page_size": int(page_size)}
        )
        separator = "&" if "?" in self.url else "?"
        return f"{self.url}{separator}{query}"

    def fetch(self, *, page_size=None, max_records=None, timeout=None):
        token = self.token()  # raises before any network call if unset
        page_size = int(
            page_size or getattr(settings, "WEBSHARE_PAGE_SIZE", DEFAULT_PAGE_SIZE)
        )
        page_size = max(1, min(page_size, 1000))
        max_records = int(
            max_records
            if max_records is not None
            else getattr(settings, "PROVIDER_SYNC_MAX_RECORDS", DEFAULT_MAX_RECORDS)
        )
        timeout = float(
            timeout
            if timeout is not None
            else getattr(settings, "PROVIDER_SYNC_TIMEOUT_SECONDS", DEFAULT_TIMEOUT)
        )

        listing = ProviderListing()
        url = self.first_url(page_size)
        seen_urls = set()

        while url:
            if listing.pages >= MAX_PAGES:
                listing.errors.append(
                    f"stopped after {MAX_PAGES} pages; the provider's `next` "
                    f"link did not terminate"
                )
                listing.truncated = True
                break
            if url in seen_urls:
                # A `next` that points back at a page already fetched is a
                # provider-side bug, and following it is an infinite loop
                # that looks like a hung cron job.
                listing.errors.append("the provider's `next` link repeated a page")
                listing.truncated = True
                break
            seen_urls.add(url)

            try:
                payload = self._get(url, token, timeout)
            except ProviderSyncError as exc:
                if not listing.records:
                    # Nothing at all was obtained. The caller gets an
                    # exception rather than an empty listing, because an
                    # empty listing and "the whole account vanished" are
                    # the same bytes and must not be the same conclusion.
                    raise ProviderUnavailable(str(exc)) from None
                listing.errors.append(str(exc))
                break

            listing.pages += 1
            if listing.reported_total is None:
                count = payload.get("count")
                listing.reported_total = count if isinstance(count, int) else None

            results = payload.get("results")
            if not isinstance(results, list):
                listing.errors.append(
                    f"page {listing.pages}: `results` was "
                    f"{type(results).__name__}, not a list"
                )
                break

            for index, record in enumerate(results):
                if len(listing.records) >= max_records:
                    listing.truncated = True
                    break
                try:
                    listing.records.append(self.parse_record(record))
                except ProviderRecordError as exc:
                    listing.errors.append(
                        f"page {listing.pages} record {index}: {exc}"
                    )
            if listing.truncated:
                break

            url = payload.get("next") or None
            if url is not None and not isinstance(url, str):
                listing.errors.append(
                    f"page {listing.pages}: `next` was "
                    f"{type(url).__name__}, not a URL"
                )
                break

        listing.complete = not listing.truncated and not listing.errors
        return listing

    def _get(self, url, token, timeout):
        """One page, parsed. Raises `ProviderSyncError` with a redacted,
        **body-free** message.

        The body is never quoted. It holds the account's proxy passwords
        in plaintext, so an error that included "the response was: ..."
        would write the sellable asset into a log file -- and the log file
        is exactly where it would be found later.
        """
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Token {token}",
                "Accept": "application/json",
                "User-Agent": "proxycrack-providersync/1.0",
            },
        )
        try:
            status, body = self._opener(request, timeout)
        except urllib.error.HTTPError as exc:
            # `exc` stringifies with the URL and the status; the URL
            # carries no secret (the token is a header) but the redaction
            # is applied anyway, on principle.
            raise ProviderSyncError(
                _redact(self._http_message(exc.code), token)
            ) from None
        except (urllib.error.URLError, OSError) as exc:
            raise ProviderSyncError(
                _redact(f"network error: {type(exc).__name__}", token)
            ) from None

        if status != 200:
            raise ProviderSyncError(_redact(self._http_message(status), token))

        try:
            payload = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            raise ProviderSyncError(
                "the provider returned a body that is not JSON (body not "
                "quoted: it carries proxy passwords)"
            ) from None
        if not isinstance(payload, dict):
            raise ProviderSyncError(
                f"the provider returned a JSON {type(payload).__name__}, "
                f"not an object"
            )
        return payload

    @staticmethod
    def _http_message(status):
        """Status-code prose. No body, no URL with query, no token."""
        if status in (401, 403):
            return (
                f"HTTP {status}: the provider rejected the API token. It may "
                f"be revoked, expired, or from a different account. Check "
                f"WEBSHARE_API_TOKEN in backend/.env -- do not paste its "
                f"value anywhere to check it."
            )
        if status == 429:
            return (
                "HTTP 429: the provider is rate limiting us. Lower "
                "WEBSHARE_PAGE_SIZE or run the sync less often."
            )
        if 500 <= status < 600:
            return f"HTTP {status}: the provider's API failed."
        return f"HTTP {status}: unexpected response status."

    # -- mapping --------------------------------------------------------

    def parse_record(self, record):
        """One `results[]` entry -> `ProviderProxy`.

        Raises `ProviderRecordError` for a record that cannot identify an
        endpoint. Such a record is counted as failed and **changes
        nothing**: it is never allowed to become an update that blanks
        half a row.
        """
        if not isinstance(record, dict):
            raise ProviderRecordError(
                f"expected an object, got {type(record).__name__}"
            )

        external_id = _text(record.get("id"))
        if not external_id:
            raise ProviderRecordError("no `id`; cannot be reconciled")

        host = _text(record.get("proxy_address"))
        if not host:
            raise ProviderRecordError(
                f"id {external_id}: no `proxy_address`"
            )

        try:
            port = int(str(record.get("port")).strip())
        except (TypeError, ValueError):
            raise ProviderRecordError(
                f"id {external_id}: `port` is not a number"
            ) from None
        if not 1 <= port <= 65535:
            raise ProviderRecordError(f"id {external_id}: port {port} out of range")

        return ProviderProxy(
            external_id=external_id[:128],
            host=host[:255],
            port=port,
            protocol=self.protocol,
            # `_stated` collapses "key absent" and "key present but empty"
            # onto None. See the module docstring: on this path they mean
            # the same thing, and neither may blank a stored credential.
            username=_stated(record.get("username")),
            password=_stated(record.get("password")),
            country=_stated(record.get("country_code"), upper=True),
            city=_stated(record.get("city_name")),
            asn=_asn(record.get("asn_number")),
            asn_org=_text(record.get("asn_name"))[:200],
            valid=bool(record.get("valid", True)),
            country_confident=bool(record.get("high_country_confidence", True)),
        )


def _urllib_opener(request, timeout):
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def _stated(value, *, upper=False):
    """`None` unless the provider stated a non-empty value."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.upper() if upper else text


def _asn(value):
    """`asn_number` as an int. Tolerates `20738`, `"20738"` and `"AS20738"`."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if text.startswith("AS"):
        text = text[2:]
    try:
        asn = int(text)
    except ValueError:
        return None
    return asn if asn > 0 else None


# ---------------------------------------------------------------------------
# The fake
# ---------------------------------------------------------------------------


class FakeProviderSync(ProviderSync):
    """Deterministic listings. No sockets, no clock, no network.

    Scripted either as a flat sequence of `ProviderProxy` or as a list of
    pages, so a test can prove the record cap and the page tally without
    HTTP:

        FakeProviderSync([record_a, record_b])
        FakeProviderSync(pages=[[record_a], [record_b]])
        FakeProviderSync([record_a], complete=False)   # a partial listing
        FakeProviderSync(missing_credentials=True)     # the fail-loud path

    `calls` records each fetch's arguments, which is how a test proves the
    page size and cap were actually passed through rather than defaulted.
    """

    name = "fake"
    provider = "webshare"

    def __init__(
        self,
        records=None,
        *,
        pages=None,
        provider=None,
        complete=True,
        truncated=False,
        errors=None,
        unavailable=None,
        missing_credentials=False,
    ):
        if pages is not None:
            self.pages = [list(page) for page in pages]
        else:
            self.pages = [list(records or [])]
        self.provider = provider or self.provider
        self._complete = complete
        self._truncated = truncated
        self._errors = list(errors or [])
        self._unavailable = unavailable
        self._missing_credentials = missing_credentials
        self.calls: List[dict] = []

    def fetch(self, *, page_size=None, max_records=None, timeout=None):
        self.calls.append(
            {
                "page_size": page_size,
                "max_records": max_records,
                "timeout": timeout,
            }
        )
        if self._missing_credentials:
            raise ProviderCredentialsMissing(
                "fake: no API token configured for this provider"
            )
        if self._unavailable:
            raise ProviderUnavailable(str(self._unavailable))

        listing = ProviderListing(errors=list(self._errors))
        cap = max_records if max_records is not None else None
        for page in self.pages:
            listing.pages += 1
            for record in page:
                if cap is not None and len(listing.records) >= cap:
                    listing.truncated = True
                    break
                listing.records.append(record)
            if listing.truncated:
                break
        listing.reported_total = sum(len(page) for page in self.pages)
        listing.truncated = listing.truncated or self._truncated
        listing.complete = (
            self._complete and not listing.truncated and not listing.errors
        )
        return listing


def get_provider_sync() -> ProviderSync:
    """Instantiate the configured adapter.

    A dotted path for the same reason `PROXY_PROBER` and
    `IP_INTEL_BACKEND` are: a test swaps in `FakeProviderSync` with
    `override_settings` and nothing has to thread an argument through a
    command, a view and a reconciler.
    """
    path = getattr(
        settings, "PROVIDER_SYNC_BACKEND", "proxies.providers.WebshareSync"
    )
    return import_string(path)()


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

#: Columns the sync is allowed to write on an existing row, and nothing
#: else. Notably absent: `tier` (an entitlement decision the operator
#: owns), `is_alive` and every other health column (derived from
#: `ProxyProbe` by `proxies.health`, and a sync that touched them would be
#: asserting a measurement it never made).
SYNCED_FIELDS = ("host", "port", "username", "password", "country", "city")


def _match_key(provider, external_id):
    return (provider_key(provider), str(external_id))


def sync_provider(
    adapter: Optional[ProviderSync] = None,
    *,
    provider: Optional[str] = None,
    limit: Optional[int] = None,
    page_size: Optional[int] = None,
    timeout: Optional[float] = None,
    dry_run: bool = False,
    write_intel: bool = True,
    triggered_by=None,
    now=None,
) -> SyncOutcome:
    """Fetch the provider's listing and reconcile the inventory against it.

    Reconciles on **(provider, external_id)**, never on host:port. The
    difference is the whole feature: a provider rotating an address is one
    endpoint changing its IP, and matching on host:port would record it as
    one endpoint dying and an unrelated one being born -- throwing away the
    probe history of a proxy that never left, and starting the replacement
    at the unprobed score with no record that it is the same rental.

    Returns a `SyncOutcome`. A `ProviderSyncRun` row is written either way,
    including for a dry run and including for a failure: "the 4am sync has
    been failing for a fortnight" is only answerable if the failures left
    rows.
    """
    now = now or timezone.now()
    adapter = adapter or get_provider_sync()
    label = (provider or getattr(adapter, "provider", "") or adapter.name).strip()

    outcome = SyncOutcome(provider=label, source=adapter.name, dry_run=dry_run)
    run = ProviderSyncRun(
        provider=label[:100],
        source=adapter.name[:64],
        dry_run=dry_run,
        triggered_by=triggered_by if _is_real_user(triggered_by) else None,
        started_at=now,
    )

    try:
        listing = adapter.fetch(
            page_size=page_size, max_records=limit, timeout=timeout
        )
    except ProviderSyncError as exc:
        # Includes the missing-token case. The run is recorded as failed
        # so the operator can see that the schedule is firing and getting
        # nowhere, which is the failure that otherwise looks like silence.
        outcome.status = ProviderSyncStatus.FAILED
        outcome.errors.append(str(exc))
        run.status = ProviderSyncStatus.FAILED
        run.errors = str(exc)[:4000]
        run.finished_at = timezone.now()
        run.save()
        outcome.run = run
        # A bare `raise`, not a re-wrap. The *kind* of failure is what the
        # caller branches on -- a missing token is a configuration error
        # the operator must fix, a 502 is a vendor having a bad morning --
        # and flattening both to `ProviderSyncError` would have made the
        # command and the API endpoint report them identically.
        raise

    outcome.listing = listing
    outcome.errors.extend(listing.errors)

    existing = _load_managed(label)
    seen = set()

    for record in listing.records:
        key = _match_key(label, record.external_id)
        if key in seen:
            outcome.outcomes.append(
                RecordOutcome(
                    action=SyncAction.FAILED,
                    external_id=record.external_id,
                    endpoint=record.endpoint,
                    detail="the provider listed this id twice in one run",
                )
            )
            continue
        seen.add(key)
        outcome.outcomes.append(
            _reconcile_one(record, existing.get(key), label, dry_run)
        )

    # Vanished endpoints -- ONLY from a complete listing. A page that
    # failed looks exactly like an inventory that halved, and the second
    # reading would disable rows that never went anywhere.
    if listing.complete:
        for key, proxy in existing.items():
            if key in seen:
                continue
            outcome.outcomes.append(_disable_vanished(proxy, dry_run))
    elif existing:
        missing = len(existing) - len([k for k in seen if k in existing])
        if missing > 0:
            outcome.errors.append(
                f"{missing} known endpoint(s) were absent from this listing "
                f"and were NOT disabled: the listing is incomplete, and an "
                f"absence cannot be distinguished from a page that failed."
            )

    # Intel is filled from the records the provider actually returned,
    # whatever happened to the inventory rows -- the ASN is a fact about
    # an address and is worth caching even for an endpoint that failed to
    # reconcile.
    if write_intel and not dry_run:
        outcome.intel_written, outcome.intel_skipped = _write_intel(
            listing.records, adapter.name, now=now
        )
    elif write_intel and dry_run:
        outcome.intel_written, outcome.intel_skipped = _preview_intel(
            listing.records, now=now
        )

    outcome.counts = _tally(outcome.outcomes)
    outcome.status = (
        ProviderSyncStatus.PARTIAL if not listing.complete else ProviderSyncStatus.OK
    )

    _fill_run(run, outcome, listing)
    run.finished_at = timezone.now()
    run.save()
    outcome.run = run
    return outcome


def _is_real_user(user):
    return bool(user is not None and getattr(user, "pk", None) and getattr(user, "is_authenticated", False))


def _load_managed(label):
    """`{(provider_key, external_id): Proxy}` for rows this sync manages.

    Scoped by provider **and** a non-blank external_id, which is what
    keeps a sync's blast radius inside the endpoints it bought. A CSV row,
    a hand-added proxy, or another vendor's inventory is invisible here and
    can therefore never be disabled by somebody else's listing.
    """
    managed = {}
    queryset = Proxy.objects.filter(provider__iexact=label).exclude(external_id="")
    for proxy in queryset:
        managed[_match_key(proxy.provider, proxy.external_id)] = proxy
    return managed


def _reconcile_one(record, proxy, label, dry_run):
    """Create, adopt, or update one endpoint."""
    if proxy is None:
        adopted = _find_adoptable(record, label)
        if adopted is not None:
            return _adopt(adopted, record, label, dry_run)
        return _create(record, label, dry_run)
    return _update(proxy, record, label, dry_run)


def _find_adoptable(record, label):
    """An existing row for this endpoint that no sync has claimed yet.

    THE MIGRATION CASE. Every proxy in the database before this feature
    existed was typed in by hand or imported from a CSV, so it carries no
    `external_id` -- and the first sync would otherwise try to create a
    second row at the same host:port, hit `uniq_proxy_endpoint`, and report
    a failure for an endpoint that is sitting right there, already probed.

    Matching on host:port is correct **here and only here**: on the first
    encounter there is no external_id to match on, so the endpoint tuple is
    the only identity available. Afterwards the row carries the provider's
    id and every later run matches on that, which is what makes a
    subsequent rotation a rename rather than a replacement.

    A row that already carries a different external_id is **not**
    adoptable: that is a genuine collision and is reported rather than
    silently resolved.
    """
    return (
        Proxy.objects.filter(
            protocol=record.protocol, host=record.host, port=record.port
        )
        .filter(external_id="")
        .first()
    )


#: Columns a provider may fill but may never blank. `host` and `port` are
#: not here because a record without them never gets this far -- the
#: adapter rejects it.
OPTIONAL_SYNCED_FIELDS = ("username", "password", "country", "city")


def _stated_values(record):
    """The fields the provider actually stated, ready to assign.

    This is the function that cannot be got wrong, and it is deliberately
    the *reconciler's* function rather than the adapter's. The adapter
    already maps an absent key and an empty string onto `None`; doing the
    same check again here means a second adapter, written later by
    somebody who did not read that part of the docstring, still cannot
    blank a credential. One choke point, checked twice.

    An empty value is treated as unstated, which is where this diverges
    from `proxies.importers`. There a present-but-empty CSV cell is a
    deliberate clear, because a human authored the file and can mean it.
    An empty string in an API response at 4am means a truncated page or a
    vendor bug, and the credentials are Fernet-encrypted with no plaintext
    copy anywhere -- so a sync can fill a field and never empty one.
    Clearing a credential stays a deliberate act through the console.
    """
    values = {"host": record.host, "port": record.port}
    for name in OPTIONAL_SYNCED_FIELDS:
        value = getattr(record, name)
        if value is None or value == "":
            continue
        values[name] = value
    return values


def _create(record, label, dry_run):
    values = _stated_values(record)
    proxy = Proxy(
        protocol=record.protocol,
        provider=label,
        external_id=record.external_id,
        tier=str(getattr(settings, "PROVIDER_SYNC_TIER", ProxyTier.PREMIUM)),
        **values,
    )
    if dry_run:
        return RecordOutcome(
            action=SyncAction.CREATED,
            external_id=record.external_id,
            endpoint=record.endpoint,
            fields=tuple(sorted(values)),
        )
    try:
        with transaction.atomic():
            proxy.save()
    except IntegrityError:
        return _collision(record, label)
    return RecordOutcome(
        action=SyncAction.CREATED,
        external_id=record.external_id,
        endpoint=record.endpoint,
        proxy_id=proxy.pk,
        fields=tuple(sorted(values)),
    )


def _collision(record, label):
    """Another row already occupies this host:port under a different id.

    Reported, never resolved. Resolving it would mean either deleting the
    other row -- which takes its probe history and may be referenced by a
    saved chain -- or rewriting its address, which would make the history
    a record of somewhere else. Both destroy evidence to tidy up a tally.

    It happens when a provider re-issues an address that one of its own
    retired endpoints still holds in our inventory. The operator decides,
    and the message tells them what they are deciding between.
    """
    other = Proxy.objects.filter(
        protocol=record.protocol, host=record.host, port=record.port
    ).first()
    detail = (
        f"{record.endpoint} is already held by proxy "
        f"{other.pk if other else '?'} "
        f"(external_id {other.external_id!r} provider {other.provider!r})"
        if other
        else f"{record.endpoint} collided with an existing row"
    )
    return RecordOutcome(
        action=SyncAction.FAILED,
        external_id=record.external_id,
        endpoint=record.endpoint,
        proxy_id=other.pk if other else None,
        detail=(
            f"{detail}. Nothing was changed: resolving this automatically "
            f"would mean deleting that row's probe history or rewriting its "
            f"address. Disable or retire it by hand, then re-run."
        ),
    )


def _adopt(proxy, record, label, dry_run):
    """Claim a pre-existing row for the provider's id. Keeps everything."""
    changed = _apply(proxy, record, label, claim=True)
    if not dry_run:
        _save(proxy, changed)
    return RecordOutcome(
        action=SyncAction.ADOPTED,
        external_id=record.external_id,
        endpoint=record.endpoint,
        proxy_id=proxy.pk,
        fields=tuple(sorted(changed)),
        detail=(
            f"existing proxy {proxy.pk} matched on host:port and had no "
            f"external_id; claimed rather than duplicated "
            f"({proxy.probe_count} probe(s) of history kept)"
        ),
    )


def _update(proxy, record, label, dry_run):
    was_disabled = not proxy.is_enabled
    reenabled = was_disabled and proxy.disabled_by_sync
    changed = _apply(proxy, record, label, reenable=reenabled)
    if not dry_run:
        _save(proxy, changed)

    if reenabled:
        action = SyncAction.REENABLED
        detail = "the provider is listing this endpoint again"
    elif changed:
        action = SyncAction.UPDATED
        detail = (
            "the provider rotated this endpoint's address"
            if {"host", "port"} & set(changed)
            else ""
        )
        if was_disabled:
            detail = (
                detail + "; " if detail else ""
            ) + "left disabled: an operator switched it off, not a sync"
    else:
        action = SyncAction.UNCHANGED
        detail = (
            "left disabled: an operator switched it off, not a sync"
            if was_disabled
            else ""
        )
    return RecordOutcome(
        action=action,
        external_id=record.external_id,
        endpoint=record.endpoint,
        proxy_id=proxy.pk,
        fields=tuple(sorted(changed)),
        detail=detail,
    )


def _apply(proxy, record, label, *, claim=False, reenable=False):
    """Set the stated fields on `proxy`; return the names that moved.

    Compares before assigning, so an unchanged endpoint produces no write
    at all -- which is what makes the second run of a sync genuinely a
    no-op rather than a no-op-shaped row rewrite that bumps `updated_at`
    on the entire inventory every night.
    """
    changed = []
    for name, value in _stated_values(record).items():
        if getattr(proxy, name) != value:
            setattr(proxy, name, value)
            changed.append(name)

    if claim:
        if proxy.external_id != record.external_id:
            proxy.external_id = record.external_id
            changed.append("external_id")
        if provider_key(proxy.provider) != provider_key(label):
            proxy.provider = label
            changed.append("provider")

    if reenable:
        proxy.is_enabled = True
        proxy.disabled_by_sync = False
        changed.extend(["is_enabled", "disabled_by_sync"])

    return changed


def _save(proxy, changed):
    if not changed:
        return
    fields = list(dict.fromkeys(changed))
    if "updated_at" not in fields and hasattr(proxy, "updated_at"):
        fields.append("updated_at")
    proxy.save(update_fields=fields if proxy.pk else None)


def _disable_vanished(proxy, dry_run):
    """The provider stopped listing this endpoint.

    `is_enabled = False`, and **never a delete**. The probe history is the
    evidence base for every quality score in the product and a saved chain
    may reference the row through `ChainHop.proxy`, which is `PROTECT`.
    Disabling also leaves the row ready to be re-enabled if the endpoint
    comes back, which is a thing provider inventories genuinely do.
    """
    if not proxy.is_enabled:
        return RecordOutcome(
            action=SyncAction.UNCHANGED,
            external_id=proxy.external_id,
            endpoint=proxy.endpoint,
            proxy_id=proxy.pk,
            detail="absent from the listing and already disabled",
        )
    if not dry_run:
        proxy.is_enabled = False
        proxy.disabled_by_sync = True
        proxy.save(update_fields=["is_enabled", "disabled_by_sync", "updated_at"])
    return RecordOutcome(
        action=SyncAction.DISABLED,
        external_id=proxy.external_id,
        endpoint=proxy.endpoint,
        proxy_id=proxy.pk,
        fields=("is_enabled",),
        detail="absent from the provider's listing; disabled, not deleted",
    )


def _write_intel(records, source_name, *, now=None):
    """Fill `IpIntelRecord` from the provider's own ASN fields.

    The high-value half of this feature. R2's diversity scoring needs one
    fact per hop that the inventory does not hold -- which AS originates
    the address -- and on a host with no usable DNS resolver
    (`IP_INTEL_DNS_RESOLVERS` unset, which is the default and deliberately
    has no public fallback) the Cymru lookup cannot run and **every hop
    reports as unknown**. The provider already knows the answer and sends
    it unasked.

    Written through `store_provider_facts`, which marks the row as
    provider-asserted and refuses to overwrite a fresher registry lookup.
    See that function for the precedence rule and why it exists: a vendor
    reselling one network under three names has an interest in its
    inventory looking diverse, and this is the one place that interest
    could reach the scoring.
    """
    written = skipped = 0
    for record in records:
        facts = record.intel_facts(source_name)
        if facts is None:
            skipped += 1
            continue
        _record, status = store_provider_facts(facts, now=now)
        if status == INTEL_WRITTEN:
            written += 1
        else:
            skipped += 1
    return written, skipped


def _preview_intel(records, *, now=None):
    """What `_write_intel` would do. Writes nothing."""
    from proxies.intel import cached_facts, is_provider_source

    hosts = [normalise_host(record.host) for record in records]
    fresh = cached_facts(hosts, now=now)
    written = skipped = 0
    for record in records:
        if record.asn is None:
            skipped += 1
            continue
        existing = fresh.get(normalise_host(record.host))
        if existing is not None and not is_provider_source(existing.source):
            skipped += 1
        else:
            written += 1
    return written, skipped


def _tally(outcomes):
    counts = {
        action: 0
        for action in (
            SyncAction.CREATED,
            SyncAction.ADOPTED,
            SyncAction.UPDATED,
            SyncAction.REENABLED,
            SyncAction.DISABLED,
            SyncAction.UNCHANGED,
            SyncAction.FAILED,
        )
    }
    for outcome in outcomes:
        counts[outcome.action] = counts.get(outcome.action, 0) + 1
    return counts


def _fill_run(run, outcome, listing):
    run.status = outcome.status
    run.pages = listing.pages
    run.fetched_count = len(listing.records)
    run.created_count = outcome.count(SyncAction.CREATED)
    run.updated_count = outcome.count(SyncAction.UPDATED)
    run.adopted_count = outcome.count(SyncAction.ADOPTED)
    run.disabled_count = outcome.count(SyncAction.DISABLED)
    run.reenabled_count = outcome.count(SyncAction.REENABLED)
    run.unchanged_count = outcome.count(SyncAction.UNCHANGED)
    run.failed_count = outcome.count(SyncAction.FAILED)
    run.invalid_count = sum(1 for record in listing.records if not record.valid)
    run.intel_written = outcome.intel_written
    run.intel_skipped = outcome.intel_skipped

    errors = list(outcome.errors)
    for item in outcome.outcomes:
        if item.action == SyncAction.FAILED:
            errors.append(f"{item.external_id}: {item.detail}")
    if len(errors) > MAX_STORED_ERRORS:
        errors = errors[:MAX_STORED_ERRORS] + [
            f"... further errors truncated at {MAX_STORED_ERRORS}"
        ]
    # Nothing in `errors` carries a credential or a token: per-record
    # messages name ids and endpoints, page messages name status codes,
    # and no response body is ever quoted. `ProviderSyncRun.errors` is
    # rendered in the admin SPA and may be exported.
    run.errors = "\n".join(errors)[:8000]
