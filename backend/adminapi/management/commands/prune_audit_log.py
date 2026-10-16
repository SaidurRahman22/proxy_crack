"""Delete admin audit history past its retention window.

    manage.py prune_audit_log                        # reports, deletes nothing
    manage.py prune_audit_log --yes                  # settings default (365d)
    manage.py prune_audit_log --older-than-days 400 --yes
    manage.py prune_audit_log --older-than-days 30   # refused: below the floor

Deliberately harder to use than `prune_probes`, and the difference is the
whole design:

**It does nothing without `--yes`.** A dry run is the default, not a flag.
`prune_probes` deletes telemetry that the next run regenerates; this
deletes the only record of who did what, and that is not recoverable from
anywhere. A command where the destructive behaviour is what you get by
forgetting an argument is a command that eventually destroys something by
accident.

**It refuses to prune below a floor.** `--older-than-days` cannot go under
`settings.ADMIN_AUDIT_MIN_RETENTION_DAYS` (90). The floor exists because
the realistic attack is not "delete everything" -- that is conspicuous --
but "run the retention job with a small number and call it a typo". The
command will not do it, and the database refuses anything inside 30 days
regardless (see `adminapi/migrations/0001_initial.py`).

**The default retention is an order of magnitude longer than the probe
retention**: 365 days against 30. Probe rows are telemetry and the newest
ones answer every question worth asking. Audit rows are evidence, and the
questions asked of them -- "when did this account start revealing
credentials", "who changed the free plan's tier in March" -- are about the
distant past, asked after an incident makes somebody look.

**The prune is itself audited.** An `audit_log_pruned` event is written
*after* the delete, so the record of the deletion is outside what was
deleted and cannot be removed by the same run.

Run it monthly at most. There is no reason for this to be in the same cron
entry as anything else.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from adminapi.audit import audit
from adminapi.models import HARD_FLOOR_DAYS, AdminAuditLog, delete_for_retention


class Command(BaseCommand):
    help = (
        "Delete AdminAuditLog rows past the retention window. Reports "
        "only unless --yes is given."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-days",
            type=int,
            default=None,
            help="Age bound (default settings.ADMIN_AUDIT_RETENTION_DAYS). "
            "Refused below settings.ADMIN_AUDIT_MIN_RETENTION_DAYS.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Actually delete. Without this the command reports what "
            "it would remove and exits, which is the default on purpose.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Rows per DELETE, so one prune does not hold a long "
            "write lock on a table every admin request appends to.",
        )

    def handle(self, *args, **options):
        days = options["older_than_days"]
        if days is None:
            days = int(getattr(settings, "ADMIN_AUDIT_RETENTION_DAYS", 365))

        floor = int(getattr(settings, "ADMIN_AUDIT_MIN_RETENTION_DAYS", 90))
        if days < floor:
            # A CommandError, not a prompt. Someone who meant it can raise
            # the floor in configuration, where the change is visible in
            # review -- which is exactly the deliberation this is for.
            raise CommandError(
                f"--older-than-days {days} is below the retention floor of "
                f"{floor} days (settings.ADMIN_AUDIT_MIN_RETENTION_DAYS). "
                f"Deleting audit history is meant to be deliberate: raise "
                f"the floor in configuration if this is genuinely intended. "
                f"The database additionally refuses any delete inside "
                f"{HARD_FLOOR_DAYS} days."
            )

        batch_size = max(1, options["batch_size"])
        verbosity = options["verbosity"]
        cutoff = timezone.now() - timedelta(days=days)

        expired = AdminAuditLog.objects.filter(created_at__lt=cutoff)
        total = expired.count()
        remaining = AdminAuditLog.objects.count()

        if not options["yes"]:
            if verbosity:
                self.stdout.write(
                    self.style.WARNING(
                        f"DRY RUN: {total} of {remaining} audit rows are "
                        f"older than {cutoff:%Y-%m-%d %H:%M} ({days} days). "
                        f"Nothing was deleted. Re-run with --yes to delete "
                        f"them."
                    )
                )
                self._preview(expired, verbosity)
            return

        deleted = self._delete(expired, batch_size)

        # Written AFTER the delete, so this row is newer than the cutoff
        # and is therefore not inside its own blast radius. An audit log
        # that cannot record its own pruning has a hole exactly the shape
        # of the thing most worth recording.
        audit(
            "audit_log_pruned",
            retained_days=days,
            cutoff=cutoff.isoformat(),
            deleted=deleted,
            remaining=AdminAuditLog.objects.count(),
            via="prune_audit_log",
        )

        if verbosity:
            self.stdout.write(
                self.style.SUCCESS(
                    f"deleted {deleted} audit rows older than "
                    f"{cutoff:%Y-%m-%d %H:%M} ({days} days retained); "
                    f"{AdminAuditLog.objects.count()} remain"
                )
            )

    def _preview(self, queryset, verbosity):
        """What a dry run shows: the shape of what would go, not a dump."""
        if verbosity < 2 or not queryset.exists():
            return
        oldest = queryset.order_by("created_at").first()
        newest = queryset.order_by("-created_at").first()
        self.stdout.write(
            f"  oldest: {oldest.created_at:%Y-%m-%d %H:%M} {oldest.action}"
        )
        self.stdout.write(
            f"  newest: {newest.created_at:%Y-%m-%d %H:%M} {newest.action}"
        )
        counts = {}
        for action in queryset.values_list("action", flat=True):
            counts[action] = counts.get(action, 0) + 1
        for action, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f"  {action}: {count}")

    def _delete(self, queryset, batch_size):
        """Batched, through the one sanctioned delete path.

        `delete_for_retention` rather than `.delete()`, which the queryset
        refuses -- see `adminapi.models`. The database trigger still
        applies beneath this and will refuse anything inside the hard
        floor, which the floor check above means we never reach.
        """
        removed = 0
        while True:
            batch = list(queryset.values_list("pk", flat=True)[:batch_size])
            if not batch:
                return removed
            count, _ = delete_for_retention(
                AdminAuditLog.objects.filter(pk__in=batch)
            )
            removed += count
            if len(batch) < batch_size:
                return removed
