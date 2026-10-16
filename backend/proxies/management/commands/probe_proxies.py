"""Probe a selectable set of proxies and refresh their health.

    manage.py probe_proxies                          # every enabled proxy
    manage.py probe_proxies --tier premium
    manage.py probe_proxies --country NL --protocol socks5
    manage.py probe_proxies --stale-first --limit 500
    manage.py probe_proxies --ids 12,13,14
    manage.py probe_proxies --recompute              # no probing; rescore

The intended production shape is a cron entry or a systemd timer running
`--stale-first --limit N` every few minutes. That spreads the load evenly,
keeps each run bounded, and needs no broker, no worker fleet and no new
dependency -- which is the right trade at this size. Two runs overlapping is
safe (see below), so a run that overshoots its interval does no harm.

**Where a queue would go.** When probe volume outgrows a cron slot, the
change is to replace the `probe_batch(...)` call in `_run_probes` with an
enqueue of one task per `ProxyTarget` and have the worker call
`record_probe_results` for its own slice. Nothing else moves: selection,
scoring and the health recompute are already independent of how the probe
got executed, and `record_probe_results` is already safe to call
concurrently from several processes. The reason it is not done now is that
a broker is an operational dependency -- a thing to deploy, monitor and
restore -- and a cron line is not.

**Safe to run repeatedly and concurrently with itself.** Probe rows are
append-only, and the denormalised fields on `Proxy` are *recomputed from
those rows* rather than incremented, so there is no counter to double-count
and no read-modify-write to lose. Two runs probing the same proxy produce
two honest observations; whichever finishes last recomputes from both. See
`proxies.health`.
"""

import time

from django.core.management.base import BaseCommand, CommandError
from django.db.models import F
from django.utils import timezone

from proxies.health import recompute_all, record_probe_results
from proxies.models import Proxy, ProxyProtocol, ProxyTier
from proxies.probing import (
    DEFAULT_CONCURRENCY,
    DEFAULT_TIMEOUT,
    get_prober,
    probe_batch,
    probe_destination,
    targets_for,
)


class Command(BaseCommand):
    help = "Probe proxies through a real CONNECT and update their health."

    def add_arguments(self, parser):
        selection = parser.add_argument_group("selection")
        selection.add_argument(
            "--ids",
            help="Comma-separated proxy ids. Overrides the other filters.",
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
            help="Probe operator-disabled proxies too. Off by default: a "
            "disabled proxy is never served, so probing it spends a "
            "connection on something no user can reach.",
        )
        selection.add_argument(
            "--stale-first",
            action="store_true",
            help="Order by least-recently-checked, never-checked first. The "
            "flag a cron entry wants.",
        )
        selection.add_argument(
            "--limit", type=int, help="Probe at most this many proxies."
        )

        execution = parser.add_argument_group("execution")
        execution.add_argument(
            "--concurrency",
            type=int,
            default=None,
            help=f"Simultaneous probes (default {DEFAULT_CONCURRENCY} or "
            f"settings.PROXY_PROBE_CONCURRENCY).",
        )
        execution.add_argument(
            "--timeout",
            type=float,
            default=None,
            help=f"Seconds per probe, whole handshake (default "
            f"{DEFAULT_TIMEOUT} or settings.PROXY_PROBE_TIMEOUT_SECONDS).",
        )
        execution.add_argument(
            "--target",
            help="host:port to CONNECT to (default "
            "settings.PROXY_PROBE_TARGET_HOST/PORT).",
        )
        execution.add_argument(
            "--recompute",
            action="store_true",
            help="Do not probe. Rebuild the health fields from existing "
            "probe rows -- what to run after changing PROXY_PROBE_WINDOW or "
            "the scoring weights, since the stored score is a cache.",
        )
        execution.add_argument(
            "--dry-run",
            action="store_true",
            help="Probe, report, write nothing.",
        )

    def handle(self, *args, **options):
        from django.conf import settings

        verbosity = options["verbosity"]
        queryset = self._select(options)

        if options["recompute"]:
            updated = recompute_all(queryset)
            if verbosity:
                self.stdout.write(
                    self.style.SUCCESS(f"recomputed health for {updated} proxies")
                )
            return

        destination = self._destination(options)
        timeout = options["timeout"] or float(
            getattr(settings, "PROXY_PROBE_TIMEOUT_SECONDS", DEFAULT_TIMEOUT)
        )
        concurrency = options["concurrency"] or int(
            getattr(settings, "PROXY_PROBE_CONCURRENCY", DEFAULT_CONCURRENCY)
        )

        # Snapshotting here, on the main thread, is what decrypts the
        # credential columns. Probe threads never touch the ORM.
        targets = targets_for(queryset.iterator(chunk_size=500))
        if not targets:
            if verbosity:
                self.stdout.write("no proxies matched")
            return

        if verbosity:
            self.stdout.write(
                f"probing {len(targets)} proxies via CONNECT to "
                f"{destination[0]}:{destination[1]} "
                f"(timeout {timeout}s, concurrency {concurrency})"
            )

        started = time.monotonic()
        outcome = probe_batch(
            get_prober(),
            targets,
            destination=destination,
            timeout=timeout,
            concurrency=concurrency,
        )
        elapsed = time.monotonic() - started

        if not options["dry_run"]:
            record_probe_results(outcome.results, now=timezone.now())

        self._report(outcome, elapsed, options, verbosity)

    # -- selection -------------------------------------------------------

    def _select(self, options):
        queryset = Proxy.objects.all()

        if options["ids"]:
            try:
                ids = [int(part) for part in options["ids"].split(",") if part.strip()]
            except ValueError:
                raise CommandError("--ids must be a comma-separated list of integers")
            if not ids:
                raise CommandError("--ids was empty")
            return queryset.filter(pk__in=ids)

        if not options["include_disabled"]:
            queryset = queryset.filter(is_enabled=True)
        if options["tier"]:
            queryset = queryset.filter(tier=options["tier"])
        if options["protocol"]:
            queryset = queryset.filter(protocol=options["protocol"])
        if options["country"]:
            queryset = queryset.filter(country__iexact=options["country"])

        if options["stale_first"]:
            # Nulls first: a proxy nothing has ever probed is the stalest
            # thing in the table, not the freshest.
            queryset = queryset.order_by(F("last_checked").asc(nulls_first=True), "pk")
        else:
            queryset = queryset.order_by("pk")

        if options["limit"]:
            queryset = queryset[: options["limit"]]
        return queryset

    def _destination(self, options):
        if not options["target"]:
            return probe_destination()
        raw = options["target"]
        host, _, port = raw.rpartition(":")
        if not host or not port.isdigit():
            raise CommandError(f"--target must be host:port, got {raw!r}")
        return host, int(port)

    # -- reporting -------------------------------------------------------

    def _report(self, outcome, elapsed, options, verbosity):
        if not verbosity:
            return

        attempted = len(outcome.results)
        line = (
            f"{attempted} probed in {elapsed:.1f}s: "
            f"{outcome.succeeded} ok, {outcome.failed} failed"
        )
        if options["dry_run"]:
            line += " (dry run, nothing written)"
        style = self.style.SUCCESS if not outcome.failed else self.style.WARNING
        self.stdout.write(style(line))

        if outcome.failed and verbosity > 1:
            kinds = {}
            for _pk, result, _target in outcome.results:
                if not result.succeeded:
                    kinds[result.error_kind] = kinds.get(result.error_kind, 0) + 1
            for kind, count in sorted(kinds.items(), key=lambda kv: -kv[1]):
                self.stdout.write(f"  {kind}: {count}")

        if outcome.skipped:
            # A skip is OUR gap, never a verdict on the proxy, and the
            # breakdown says which gap. "4 unsupported_cipher" is a
            # roadmap item (SS2022); "4 unsupported_protocol" is an
            # inventory row with a protocol nothing here speaks. Reporting
            # one number for both leaves an operator unable to tell a
            # missing feature from bad data.
            breakdown = ", ".join(
                f"{count} {code}"
                for code, count in sorted(
                    outcome.skip_reason_counts().items(), key=lambda kv: -kv[1]
                )
            )
            self.stdout.write(
                self.style.WARNING(
                    f"{len(outcome.skipped)} skipped ({breakdown}) -- this "
                    f"prober cannot measure them. They keep the unprobed "
                    f"score rather than being recorded as dead."
                )
            )
            if verbosity > 1:
                for proxy_id, reason in outcome.skipped:
                    self.stdout.write(f"  proxy {proxy_id}: {reason}")
        if outcome.errored:
            # Our bug, not the proxy's. Nothing was recorded for these, so
            # a broken prober cannot mark the inventory dead.
            self.stderr.write(
                self.style.ERROR(
                    f"{len(outcome.errored)} probes raised; no rows written "
                    f"for them. First: {outcome.errored[0][1]}"
                )
            )
