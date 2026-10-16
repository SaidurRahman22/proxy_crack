"""Delete probe history past its retention window.

    manage.py prune_probes                      # settings defaults
    manage.py prune_probes --older-than-days 14
    manage.py prune_probes --dry-run

`ProxyProbe` is the only table here that grows without anyone doing
anything. At one probe per proxy every five minutes, 10,000 proxies produce
**2.88 million rows a day** -- a gigabyte a week, and a table whose index
stops fitting in cache long before anyone notices the disk. Leaving this
unbounded is not untidiness, it is a scheduled outage.

Retention policy
----------------
Two bounds, and both matter:

* `--older-than-days` (default `settings.PROXY_PROBE_RETENTION_DAYS`, 30).
  The age bound. **It must be at least `PROXY_PROBE_WINDOW_DAYS`** or
  pruning would delete rows the rolling window is still scoring from, and
  every proxy's quality would quietly reset. `proxies.W001` fails
  `manage.py check` if that invariant is broken, rather than leaving it to
  be discovered as a mysterious loss of health data.

* `--keep-last` (default `settings.PROXY_PROBE_WINDOW`, 50). The count
  bound, applied per proxy. It protects a rarely-probed proxy -- one whose
  entire history is older than the retention window -- from being reduced
  to zero rows and reverting to "never probed". Deleting the evidence that
  something failed every time for six months is the opposite of useful.

Thirty days of retention also leaves room to answer the questions probe
data is actually for: "was this provider degrading before it died", and
"did last Tuesday's latency spike hit one country or all of them".

Run it nightly from the same cron that runs `probe_proxies`.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from proxies.health import recompute_all
from proxies.models import Proxy, ProxyProbe


def _distinct_proxy_ids(queryset):
    """The proxies a queryset of probes touches, each exactly once.

    `.order_by()` first is load-bearing and is not a tidy-up. `ProxyProbe`
    has a `Meta.ordering`, and Django adds ordering columns to the SELECT
    list of a `.distinct()` query -- so `DISTINCT proxy_id` silently
    becomes `DISTINCT proxy_id, checked_at, id`, which de-duplicates
    nothing. Found by running `prune_probes` against seeded data and
    seeing it report ten affected proxies when the database held five.
    """
    return queryset.order_by().values_list("proxy_id", flat=True).distinct()


class Command(BaseCommand):
    help = "Delete ProxyProbe rows past the retention window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-days",
            type=int,
            default=None,
            help="Age bound (default settings.PROXY_PROBE_RETENTION_DAYS).",
        )
        parser.add_argument(
            "--keep-last",
            type=int,
            default=None,
            help="Always keep this many newest probes per proxy, however "
            "old (default settings.PROXY_PROBE_WINDOW). 0 disables.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=5000,
            help="Rows per DELETE. Keeps one prune from holding a long "
            "write lock on a table every probe run is appending to.",
        )
        parser.add_argument(
            "--no-recompute",
            action="store_true",
            help="Skip the health recompute for pruned proxies. Only safe "
            "when the retention window comfortably covers the scoring "
            "window, in which case the recompute is a no-op anyway.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted without deleting it.",
        )

    def handle(self, *args, **options):
        days = options["older_than_days"]
        if days is None:
            days = int(getattr(settings, "PROXY_PROBE_RETENTION_DAYS", 30))
        if days < 1:
            raise CommandError("--older-than-days must be at least 1")

        keep_last = options["keep_last"]
        if keep_last is None:
            keep_last = int(getattr(settings, "PROXY_PROBE_WINDOW", 50))
        if keep_last < 0:
            raise CommandError("--keep-last cannot be negative")

        batch_size = max(1, options["batch_size"])
        dry_run = options["dry_run"]
        verbosity = options["verbosity"]
        cutoff = timezone.now() - timedelta(days=days)

        expired = ProxyProbe.objects.filter(checked_at__lt=cutoff)

        if keep_last == 0:
            touched = list(_distinct_proxy_ids(expired))
            deleted = self._delete(expired, batch_size, dry_run)
            self._recompute(touched, options, dry_run, verbosity)
            self._report(deleted, cutoff, keep_last, dry_run, verbosity)
            return

        deleted = 0
        protected = 0
        # Only proxies that actually have expired rows -- there is no point
        # issuing a per-proxy query for an inventory that was imported
        # yesterday.
        proxy_ids = list(_distinct_proxy_ids(expired))
        for proxy_id in proxy_ids:
            keep = list(
                ProxyProbe.objects.filter(proxy_id=proxy_id)
                .order_by("-checked_at", "-pk")
                .values_list("pk", flat=True)[:keep_last]
            )
            queryset = ProxyProbe.objects.filter(
                proxy_id=proxy_id, checked_at__lt=cutoff
            ).exclude(pk__in=keep)
            removed = self._delete(queryset, batch_size, dry_run)
            deleted += removed
            protected += len(keep)

        self._recompute(proxy_ids, options, dry_run, verbosity)
        self._report(
            deleted,
            cutoff,
            keep_last,
            dry_run,
            verbosity,
            proxies=len(proxy_ids),
            protected=protected,
        )

    def _recompute(self, proxy_ids, options, dry_run, verbosity):
        """Rebuild health for the proxies this prune touched.

        The denormalised fields on `Proxy` are a cache of the probe table,
        and deleting rows invalidates that cache. With a sane retention
        window this is a no-op -- nothing inside the scoring window was
        removed -- but `--older-than-days` can be shorter than the window
        on any given invocation, and `--keep-last 0` can empty a proxy's
        history entirely. Without this, such a prune leaves a proxy
        reporting a success rate derived from rows that no longer exist,
        and nothing corrects it until the next probe run.

        Scoped to the proxies actually pruned rather than the whole
        inventory: a nightly prune should not be a full-table rescore.
        """
        if dry_run or options["no_recompute"] or not proxy_ids:
            return
        updated = recompute_all(Proxy.objects.filter(pk__in=proxy_ids))
        if verbosity > 1:
            self.stdout.write(f"recomputed health for {updated} pruned proxies")

    def _delete(self, queryset, batch_size, dry_run):
        if dry_run:
            return queryset.count()
        removed = 0
        while True:
            # Slice to pks first: deleting a sliced queryset directly is not
            # allowed, and this also bounds how many rows one statement
            # touches.
            batch = list(queryset.values_list("pk", flat=True)[:batch_size])
            if not batch:
                return removed
            count, _ = ProxyProbe.objects.filter(pk__in=batch).delete()
            removed += count
            if len(batch) < batch_size:
                return removed

    def _report(
        self,
        deleted,
        cutoff,
        keep_last,
        dry_run,
        verbosity,
        proxies=None,
        protected=None,
    ):
        if not verbosity:
            return
        verb = "would delete" if dry_run else "deleted"
        message = f"{verb} {deleted} probe rows older than {cutoff:%Y-%m-%d %H:%M}"
        if keep_last:
            message += f", keeping the newest {keep_last} per proxy"
        if proxies is not None:
            message += f" ({proxies} proxies had expired rows"
            if protected is not None:
                message += f", {protected} rows protected by --keep-last"
            message += ")"
        self.stdout.write(self.style.SUCCESS(message))
        if verbosity > 1:
            self.stdout.write(f"{ProxyProbe.objects.count()} probe rows remain")
