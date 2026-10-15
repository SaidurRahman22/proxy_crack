"""Seed the baseline plans: one free tier, one paid tier.

Idempotent. By default an existing plan with the same `code` is left alone so
the command can be re-run after an operator has tuned limits in the admin;
pass --update to overwrite them.

    ./.venv/Scripts/python.exe manage.py seed_plans
    ./.venv/Scripts/python.exe manage.py seed_plans --update
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from billing.models import Plan
from proxies.models import ProxyTier

GIB = 1024 ** 3

PLANS = [
    {
        "code": "free",
        "name": "Free",
        "description": "Single hop, free-tier proxies only.",
        "price": Decimal("0.00"),
        "currency": "USD",
        "duration_days": 0,  # does not expire
        "max_chain_length": 1,  # graph.md section 4: free tier = 1
        "max_devices": 1,
        "bandwidth_limit_bytes": 5 * GIB,
        "max_proxy_tier": ProxyTier.FREE,
        "is_free_tier": True,
        "is_active": True,
        "sort_order": 0,
        "play_product_id": "",
    },
    {
        "code": "pro-monthly",
        "name": "Pro Monthly",
        "description": "Up to three hops, premium proxies, unmetered.",
        "price": Decimal("7.99"),
        "currency": "USD",
        "duration_days": 30,
        "max_chain_length": 3,
        "max_devices": 3,
        "bandwidth_limit_bytes": 0,  # unmetered
        "max_proxy_tier": ProxyTier.PREMIUM,
        "is_free_tier": False,
        "is_active": True,
        "sort_order": 10,
        "play_product_id": "pro_monthly",
    },
]


class Command(BaseCommand):
    help = "Create the baseline free and paid plans."

    def add_arguments(self, parser):
        parser.add_argument(
            "--update",
            action="store_true",
            help="Overwrite existing plans that share a code.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        overwrite = options["update"]
        for spec in PLANS:
            code = spec["code"]
            defaults = {key: value for key, value in spec.items() if key != "code"}
            if overwrite:
                plan, created = Plan.objects.update_or_create(code=code, defaults=defaults)
                verb = "created" if created else "updated"
            else:
                plan, created = Plan.objects.get_or_create(code=code, defaults=defaults)
                verb = "created" if created else "unchanged"
            style = self.style.SUCCESS if created else self.style.NOTICE
            self.stdout.write(
                style(
                    f"{verb}: {plan.code} "
                    f"(max_chain_length={plan.max_chain_length}, "
                    f"max_proxy_tier={plan.max_proxy_tier})"
                )
            )
