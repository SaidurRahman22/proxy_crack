"""Reconcile the proxy inventory against the provider's own listing.

    manage.py sync_webshare                       # DRY RUN -- reports, writes nothing
    manage.py sync_webshare --yes                 # actually reconcile
    manage.py sync_webshare --yes --limit 50      # bounded
    manage.py sync_webshare --yes -v 2            # one line per endpoint
    manage.py sync_webshare --yes --no-intel      # skip the ASN cache fill

The production shape is a timer entry: `sync_webshare --yes`, hourly or
daily. Nothing here needs a queue or a broker -- it is one authenticated
GET per page and a handful of row updates, and buying an operational
dependency for that would cost more than the feature.

**It does nothing without `--yes`**, following `prune_audit_log` rather
than `probe_proxies`, and the difference between those two is the reason.
A probe run writes telemetry that the next run regenerates. This one
disables rows in the sellable inventory on the strength of a third party's
response, and the failure mode -- a vendor API hiccup read as "everything
vanished" -- is precisely the one worth making an operator type a word for.
The dry run prints the same tally the real run would produce, so the
rehearsal is informative rather than ceremonial.

**`--limit` truncates the listing, and a truncated listing disables
nothing.** This is not a safety flag bolted on: an endpoint missing from a
partial listing is indistinguishable from an endpoint missing because the
page it was on never arrived. The run is reported as PARTIAL and says how
many known endpoints it declined to judge.

Despite the name it drives whatever `settings.PROVIDER_SYNC_BACKEND`
selects, and `--backend` overrides that per run. The name matches the one
provider that exists today; a second vendor gets a second settings value
rather than a second command.

WHAT IT NEVER PRINTS
--------------------
The API token and the proxy passwords. Per-endpoint output names ids,
addresses and the *names* of the columns that changed -- never their
values. Terminal output gets pasted into tickets.
"""

import time

from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string

from proxies.models import ProviderSyncStatus, Proxy
from proxies.providers import (
    ProviderCredentialsMissing,
    ProviderSyncError,
    SyncAction,
    get_provider_sync,
    sync_provider,
)

#: Printed in the order an operator cares about: what appeared, what went
#: away, what stayed put.
REPORT_ORDER = (
    SyncAction.CREATED,
    SyncAction.ADOPTED,
    SyncAction.REENABLED,
    SyncAction.UPDATED,
    SyncAction.DISABLED,
    SyncAction.UNCHANGED,
    SyncAction.FAILED,
)


class Command(BaseCommand):
    help = (
        "Reconcile the proxy inventory against the provider's listing. "
        "Reports only unless --yes is given."
    )

    def add_arguments(self, parser):
        execution = parser.add_argument_group("execution")
        execution.add_argument(
            "--yes",
            action="store_true",
            help="Actually write. Without it the command reports what it "
            "would do and exits, which is the default on purpose.",
        )
        execution.add_argument(
            "--dry-run",
            action="store_true",
            help="Explicitly request the default. Here so a cron entry can "
            "state its intent rather than rely on an omission.",
        )
        execution.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Stop after this many records. Marks the run PARTIAL, so "
            "nothing is disabled: a truncated listing cannot support the "
            "conclusion that an absent endpoint has vanished.",
        )
        execution.add_argument(
            "--page-size",
            type=int,
            default=None,
            help="Records per request (default settings.WEBSHARE_PAGE_SIZE).",
        )
        execution.add_argument(
            "--timeout",
            type=float,
            default=None,
            help="Seconds per request (default "
            "settings.PROVIDER_SYNC_TIMEOUT_SECONDS).",
        )
        execution.add_argument(
            "--backend",
            default=None,
            help="Dotted path to a ProviderSync, overriding "
            "settings.PROVIDER_SYNC_BACKEND for this run.",
        )
        execution.add_argument(
            "--provider",
            default=None,
            help="The Proxy.provider label to reconcile under, overriding "
            "the adapter's own. This is the field R2's operator-diversity "
            "check reads, so changing it between runs orphans the previous "
            "run's rows.",
        )
        execution.add_argument(
            "--no-intel",
            action="store_true",
            help="Do not populate IpIntelRecord from the provider's ASN "
            "fields. The fill is on by default because without it, on a "
            "host with no DNS resolver configured, R2 reports every hop as "
            "unknown.",
        )

    def handle(self, *args, **options):
        verbosity = options["verbosity"]
        dry_run = not options["yes"]

        if options["dry_run"] and options["yes"]:
            raise CommandError(
                "--dry-run and --yes contradict each other. --dry-run is "
                "the default; --yes is what makes the run write."
            )

        adapter = self._adapter(options["backend"])
        label = options["provider"] or getattr(adapter, "provider", adapter.name)

        if verbosity:
            self.stdout.write(
                f"provider {label!r} via {adapter.name}"
                + (" -- DRY RUN" if dry_run else "")
            )

        started = time.monotonic()
        try:
            outcome = sync_provider(
                adapter,
                provider=options["provider"],
                limit=options["limit"],
                page_size=options["page_size"],
                timeout=options["timeout"],
                dry_run=dry_run,
                write_intel=not options["no_intel"],
            )
        except ProviderCredentialsMissing as exc:
            # The message names the variable and never its value. A
            # CommandError so the exit status is non-zero and a cron
            # wrapper notices, rather than a warning in a log nobody reads.
            raise CommandError(str(exc)) from None
        except ProviderSyncError as exc:
            raise CommandError(
                f"the sync failed and nothing was changed: {exc}"
            ) from None
        elapsed = time.monotonic() - started

        self._report(outcome, elapsed, verbosity, dry_run)

    # -- adapter ---------------------------------------------------------

    def _adapter(self, path):
        if not path:
            return get_provider_sync()
        try:
            return import_string(path)()
        except ImportError as exc:
            raise CommandError(f"--backend {path!r} could not be imported: {exc}")

    # -- reporting -------------------------------------------------------

    def _report(self, outcome, elapsed, verbosity, dry_run):
        if not verbosity:
            return

        listing = outcome.listing
        self.stdout.write(
            f"fetched {len(listing.records)} record(s) over {listing.pages} "
            f"page(s) in {elapsed:.1f}s"
            + (
                f" (provider reports {listing.reported_total} in total)"
                if listing.reported_total is not None
                else ""
            )
        )

        if not listing.complete:
            # Said loudly, because the alternative reading of a tally with
            # zero disabled -- "nothing vanished" -- is the comfortable one
            # and it is wrong.
            self.stdout.write(
                self.style.WARNING(
                    "PARTIAL LISTING: the whole inventory was not "
                    "retrieved, so NOTHING was disabled. An endpoint absent "
                    "from a partial listing has not been shown to have "
                    "vanished."
                )
            )

        if verbosity > 1:
            for action in REPORT_ORDER:
                for item in outcome.of(action):
                    self.stdout.write("  " + str(item))

        for error in outcome.errors:
            self.stdout.write(self.style.WARNING(f"  ! {error}"))

        line = outcome.summary()
        if outcome.count(SyncAction.FAILED):
            style = self.style.ERROR
        elif outcome.status != ProviderSyncStatus.OK:
            style = self.style.WARNING
        else:
            style = self.style.SUCCESS
        self.stdout.write(style(line))

        verb = "would fill" if dry_run else "filled"
        self.stdout.write(
            f"IP intel: {verb} {outcome.intel_written} record(s), "
            f"{outcome.intel_skipped} skipped (no ASN given, or a fresher "
            f"registry lookup already stands)"
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN -- nothing was written. Re-run with --yes."
                )
            )
        elif verbosity:
            enabled = Proxy.objects.filter(
                provider__iexact=outcome.provider, is_enabled=True
            ).count()
            total = Proxy.objects.filter(provider__iexact=outcome.provider).count()
            self.stdout.write(
                f"inventory for {outcome.provider!r}: {enabled} enabled of "
                f"{total} known; run #{outcome.run.pk} recorded"
            )
