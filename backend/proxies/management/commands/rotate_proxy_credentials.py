"""Re-encrypt every proxy credential with the first configured key.

Step 2 of a key rotation. Reading decrypts with whichever key wrote the row
(`MultiFernet` tries all of them); saving re-encrypts with the first key in
`PROXY_CRED_KEY`. Once this finishes, the retired key can be dropped from
the environment.

    1. deploy with PROXY_CRED_KEY=<new>,<old>
    2. ./.venv/Scripts/python.exe manage.py rotate_proxy_credentials
    3. deploy with PROXY_CRED_KEY=<new>

Safe to re-run and safe to interrupt: each row is independent, and a row
already written with the new key simply gets rewritten.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from core.crypto import CredentialDecryptionError
from proxies.models import Proxy

CRED_FIELDS = ["username", "password", "ss_method"]


class Command(BaseCommand):
    help = "Re-encrypt stored proxy credentials with the current primary key."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be rewritten without writing.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Rows to stream per query (default 500).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        rewritten = skipped = failed = 0

        for proxy in Proxy.objects.all().iterator(chunk_size=options["batch_size"]):
            try:
                has_credentials = any(getattr(proxy, name) for name in CRED_FIELDS)
            except CredentialDecryptionError as exc:
                failed += 1
                self.stderr.write(self.style.ERROR(f"proxy {proxy.pk}: {exc}"))
                continue

            if not has_credentials:
                skipped += 1
                continue

            if not dry_run:
                # update_fields keeps `updated_at` untouched: a rotation is
                # not a change to the proxy itself.
                with transaction.atomic():
                    proxy.save(update_fields=CRED_FIELDS)
            rewritten += 1

        verb = "would rewrite" if dry_run else "rewrote"
        if options["verbosity"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{verb} {rewritten}, skipped {skipped} without credentials, "
                    f"{failed} unreadable"
                )
            )
        if failed:
            self.stdout.write(
                self.style.WARNING(
                    "Unreadable rows were written with a key that is no longer "
                    "in PROXY_CRED_KEY. Re-add that key and run again."
                )
            )
