"""Fill and refresh the IP-to-ASN cache the diversity analysis reads.

    manage.py refresh_ip_intel                     # every host missing or stale
    manage.py refresh_ip_intel --limit 200         # a bounded slice
    manage.py refresh_ip_intel --ids 11,12,13
    manage.py refresh_ip_intel --country NL --protocol socks5
    manage.py refresh_ip_intel --force             # re-query even fresh rows
    manage.py refresh_ip_intel --dry-run           # say what it would query

The production shape is a daily cron entry with `--limit`. Unlike
`probe_proxies` this does **not** want to run every few minutes: an ASN
assignment changes on the order of years, the default TTL is a month, and
the upstream source is a free service that owes us nothing.

**Serial, and deliberately slow.** `probe_proxies` runs a thread pool
because it spends our own bandwidth against proxies we pay for. This spends
somebody else's free service, so lookups go one at a time under
`IP_INTEL_RATE_LIMIT_PER_MINUTE`. A run over ten thousand distinct hosts
takes hours by design; `--limit` is how it is spread across days.

**Hosts, not proxies.** A provider sells hundreds of endpoints behind one
hostname, so the unit of work is the distinct host. Ten thousand proxies on
forty hostnames is forty lookups.
"""

import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import F
from django.utils import timezone

from proxies.intel import (
    RateLimiter,
    cached_facts,
    get_ip_intel,
    lookup_hosts,
    normalise_host,
)
from proxies.models import IpIntelRecord, Proxy, ProxyProtocol, ProxyTier


class Command(BaseCommand):
    help = "Look up the originating ASN for proxy hosts and cache the result."

    def add_arguments(self, parser):
        selection = parser.add_argument_group("selection")
        selection.add_argument(
            "--ids",
            help="Comma-separated proxy ids. Overrides the other filters.",
        )
        selection.add_argument(
            "--hosts",
            help="Comma-separated hosts to look up directly. They need not "
            "exist in the inventory -- useful for evaluating a vendor's "
            "sample list before importing it.",
        )
        selection.add_argument(
            "--tier", choices=[value for value, _ in ProxyTier.choices]
        )
        selection.add_argument(
            "--protocol", choices=[value for value, _ in ProxyProtocol.choices]
        )
        selection.add_argument("--country", help="Matched case-insensitively.")
        selection.add_argument(
            "--include-disabled",
            action="store_true",
            help="Include operator-disabled proxies. Off by default: a "
            "disabled proxy is in no chain, so its ASN answers no question.",
        )
        selection.add_argument(
            "--limit",
            type=int,
            help="Look up at most this many distinct hosts. The flag a cron "
            "entry wants.",
        )

        execution = parser.add_argument_group("execution")
        execution.add_argument(
            "--force",
            action="store_true",
            help="Re-query hosts whose cached record is still fresh. Rarely "
            "right: the cache is what keeps us welcome at a free service.",
        )
        execution.add_argument(
            "--timeout",
            type=float,
            default=None,
            help="Seconds per lookup (default "
            "settings.IP_INTEL_TIMEOUT_SECONDS).",
        )
        execution.add_argument(
            "--rate",
            type=float,
            default=None,
            help="Lookups per minute (default "
            "settings.IP_INTEL_RATE_LIMIT_PER_MINUTE).",
        )
        execution.add_argument(
            "--dry-run",
            action="store_true",
            help="List the hosts that would be queried and stop.",
        )

    def handle(self, *args, **options):
        verbosity = options["verbosity"]
        hosts = self._select_hosts(options)

        if not hosts:
            if verbosity:
                self.stdout.write("no hosts matched")
            return

        now = timezone.now()
        if not options["force"]:
            fresh = cached_facts(hosts, now=now)
            pending = [host for host in hosts if host not in fresh]
        else:
            fresh = {}
            pending = list(hosts)

        already_fresh = len(hosts) - len(pending)
        if options["limit"]:
            pending = pending[: options["limit"]]

        if verbosity:
            deferred = len(hosts) - already_fresh - len(pending)
            self.stdout.write(
                f"{len(hosts)} distinct host(s) selected: "
                f"{already_fresh} already fresh, {len(pending)} to look up"
                + (f", {deferred} deferred by --limit" if deferred else "")
                + (" (forced)" if options["force"] else "")
            )

        if options["dry_run"]:
            if verbosity:
                for host in pending:
                    self.stdout.write(f"  would look up {host}")
                self.stdout.write(
                    self.style.WARNING("dry run, nothing written")
                )
            return

        if not pending:
            if verbosity:
                self.stdout.write(self.style.SUCCESS("nothing to do"))
            return

        intel = get_ip_intel()
        rate = options["rate"]
        if rate is None:
            rate = getattr(settings, "IP_INTEL_RATE_LIMIT_PER_MINUTE", 60)
        limiter = RateLimiter(rate)

        if verbosity:
            self.stdout.write(
                f"looking up {len(pending)} host(s) via {intel.name} at "
                f"{rate}/min"
            )

        started = time.monotonic()
        batch = lookup_hosts(
            pending,
            intel=intel,
            force=True,  # the staleness decision was made above
            now=now,
            timeout=options["timeout"],
            limiter=limiter,
        )
        elapsed = time.monotonic() - started

        self._report(batch, pending, elapsed, verbosity)

    # -- selection -------------------------------------------------------

    def _select_hosts(self, options):
        """Distinct, normalised hosts, in a stable order."""
        hosts = []

        if options["hosts"]:
            hosts.extend(
                part.strip() for part in options["hosts"].split(",") if part.strip()
            )

        if options["ids"]:
            try:
                ids = [int(part) for part in options["ids"].split(",") if part.strip()]
            except ValueError:
                raise CommandError("--ids must be a comma-separated list of integers")
            if not ids:
                raise CommandError("--ids was empty")
            hosts.extend(
                Proxy.objects.filter(pk__in=ids).values_list("host", flat=True)
            )
        elif not options["hosts"]:
            queryset = Proxy.objects.all()
            if not options["include_disabled"]:
                queryset = queryset.filter(is_enabled=True)
            if options["tier"]:
                queryset = queryset.filter(tier=options["tier"])
            if options["protocol"]:
                queryset = queryset.filter(protocol=options["protocol"])
            if options["country"]:
                queryset = queryset.filter(country__iexact=options["country"])
            hosts.extend(queryset.order_by("pk").values_list("host", flat=True))

        ordered = []
        seen = set()
        for host in hosts:
            key = normalise_host(host)
            if key and key not in seen:
                seen.add(key)
                ordered.append(key)

        # Stalest first within the selection, so a `--limit` run spends its
        # budget on the records that have been wrong for longest. Hosts
        # with no record at all sort first: never-looked-up is staler than
        # anything with a date on it.
        known = {
            record.query: record.looked_up_at
            for record in IpIntelRecord.objects.filter(query__in=set(ordered))
        }
        ordered.sort(key=lambda host: (host in known, known.get(host)))
        return ordered

    # -- reporting -------------------------------------------------------

    def _report(self, batch, pending, elapsed, verbosity):
        if not verbosity:
            return

        if batch.unavailable:
            # The source itself could not be used. Not a statement about
            # any of these hosts, and worth saying loudly, because the
            # alternative reading -- "all our proxies are on unknown
            # networks" -- would send an operator looking for a problem
            # that is not there.
            self.stdout.write(
                self.style.ERROR(f"source unavailable: {batch.unavailable}")
            )

        resolved = len(batch.looked_up) - len(batch.failed)
        line = (
            f"{len(batch.looked_up)} looked up in {elapsed:.1f}s: "
            f"{resolved} with an origin, {len(batch.failed)} without"
        )
        style = self.style.SUCCESS if not batch.failed else self.style.WARNING
        self.stdout.write(style(line))

        if batch.failed and verbosity > 1:
            for host in batch.failed:
                record = batch.records.get(host)
                reason = (record.detail if record else "") or (
                    record.status if record else "unknown"
                )
                self.stdout.write(f"  {host}: {reason}")

        if verbosity > 1:
            asns = {
                record.asn
                for record in batch.records.values()
                if record.asn is not None
            }
            self.stdout.write(
                f"  cache now holds {IpIntelRecord.objects.count()} record(s); "
                f"this run touched {len(asns)} distinct ASN(s)"
            )
