"""Startup checks for the health subsystem.

These catch configurations that are individually plausible but jointly
wrong -- the kind that produce no error, just quietly bad data. A retention
window shorter than the scoring window does not fail; it deletes the rows
every proxy is being scored from, and the symptom is "all our proxies
reverted to unprobed last Tuesday" three weeks later.

Warnings rather than errors throughout: none of these stops the service
working, and a check that refuses to boot over a tuning parameter is a
check somebody silences.
"""

from django.core.checks import Tags, Warning, register


@register(Tags.compatibility)
def check_probe_retention_covers_window(app_configs, **kwargs):
    """Retention must outlive the scoring window."""
    from django.conf import settings

    retention = int(getattr(settings, "PROXY_PROBE_RETENTION_DAYS", 30))
    window = int(getattr(settings, "PROXY_PROBE_WINDOW_DAYS", 7))

    if retention >= window:
        return []

    return [
        Warning(
            f"PROXY_PROBE_RETENTION_DAYS ({retention}) is shorter than "
            f"PROXY_PROBE_WINDOW_DAYS ({window}). `prune_probes` will "
            f"delete probe rows that `proxies.health` is still scoring "
            f"from, so every proxy's success rate and latency percentiles "
            f"will silently reset to unprobed.",
            hint=(
                "Set PROXY_PROBE_RETENTION_DAYS to at least "
                "PROXY_PROBE_WINDOW_DAYS. There is no reason to keep less "
                "history than you score on."
            ),
            id="proxies.W001",
        )
    ]


@register(Tags.compatibility)
def check_health_thresholds(app_configs, **kwargs):
    """Thresholds that would disable the feature they configure."""
    from django.conf import settings

    problems = []

    threshold = int(getattr(settings, "PROXY_DEAD_AFTER_FAILURES", 3))
    if threshold < 2:
        problems.append(
            Warning(
                f"PROXY_DEAD_AFTER_FAILURES is {threshold}. At 1 a single "
                f"timeout kills a proxy, which is the Phase 1 behaviour "
                f"this feature replaces -- one lost packet on a loaded "
                f"network is noise, not a dead host. `proxies.health` "
                f"clamps the effective value to 2.",
                hint="Set PROXY_DEAD_AFTER_FAILURES to 3 or more.",
                id="proxies.W002",
            )
        )

    window = int(getattr(settings, "PROXY_PROBE_WINDOW", 50))
    if window < 2:
        problems.append(
            Warning(
                f"PROXY_PROBE_WINDOW is {window}. A window of one probe "
                f"makes `success_rate` a boolean again and leaves the "
                f"latency percentiles meaningless.",
                hint="Set PROXY_PROBE_WINDOW to 20 or more.",
                id="proxies.W003",
            )
        )

    weight = float(getattr(settings, "PROXY_LATENCY_WEIGHT", 0.4))
    if not 0.0 <= weight <= 1.0:
        problems.append(
            Warning(
                f"PROXY_LATENCY_WEIGHT is {weight}; it is a fraction and "
                f"must be between 0 and 1. `proxies.health` clamps it.",
                hint="0.4 means latency may move a score by at most 40%.",
                id="proxies.W004",
            )
        )
    elif weight > 0.6:
        problems.append(
            Warning(
                f"PROXY_LATENCY_WEIGHT is {weight}. Above ~0.6 latency "
                f"stops being the secondary signal: a fast proxy that "
                f"drops a third of its connections starts outranking a "
                f"reliable slow one, which is the failure the scoring "
                f"model exists to prevent.",
                hint="Keep PROXY_LATENCY_WEIGHT at or below 0.5.",
                id="proxies.W005",
            )
        )

    return problems


@register(Tags.compatibility)
def check_ip_intel_settings(app_configs, **kwargs):
    """Configurations that would make the diversity feature lie or misbehave.

    None of these stop the service: a chain with no intel is reported as
    unknown rather than as diverse, which is the whole design. They are
    warnings because the failure mode is a quieter one -- an operator who
    believes the feature is running when it cannot run at all.
    """
    from django.conf import settings

    problems = []

    ttl_days = float(getattr(settings, "IP_INTEL_TTL_DAYS", 30))
    negative_hours = float(getattr(settings, "IP_INTEL_NEGATIVE_TTL_HOURS", 6))

    if ttl_days <= 0:
        problems.append(
            Warning(
                "IP_INTEL_TTL_DAYS is 0, so every cached lookup is stale the "
                "moment it is written. Each analysis that can look things up "
                "will re-query the upstream source for every hop, which is "
                "slow for the operator and rude to a free service that may "
                "block us for it.",
                hint="An ASN assignment changes on the order of years. 30 "
                "days is the default for a reason.",
                id="proxies.W006",
            )
        )

    if negative_hours / 24.0 >= ttl_days > 0:
        problems.append(
            Warning(
                f"IP_INTEL_NEGATIVE_TTL_HOURS ({negative_hours}h) is not "
                f"shorter than IP_INTEL_TTL_DAYS ({ttl_days}d). A failed "
                f"lookup would then be cached as long as a successful one, "
                f"so a single DNS outage pins those hosts to 'unknown' for "
                f"the whole window -- and 'unknown' is the state the "
                f"diversity analysis exists to get out of.",
                hint="Keep the negative TTL in hours and the positive one "
                "in days.",
                id="proxies.W007",
            )
        )

    return problems


@register(Tags.compatibility, deploy=True)
def check_ip_intel_has_a_resolver(app_configs, **kwargs):
    """The DNS source needs a resolver, and on Windows there is no default.

    `deploy=True` on purpose. On a development machine with no
    `/etc/resolv.conf` this condition is permanent and harmless -- the
    analysis reports every hop as unknown, which it is designed to do
    honestly -- and a warning that every developer sees on every `check`
    forever is a warning everybody learns to scroll past. In a deployment
    the same condition means a feature that silently answers "unknown" to
    every question, which is worth a line in `check --deploy`.
    """
    from django.conf import settings

    backend = str(getattr(settings, "IP_INTEL_BACKEND", ""))
    if not backend.endswith("CymruDnsIpIntel"):
        return []

    from proxies.intel import configured_resolvers

    if configured_resolvers():
        return []

    return [
        Warning(
            "IP_INTEL_BACKEND is the DNS source but no resolver is "
            "available: IP_INTEL_DNS_RESOLVERS is empty and there is no "
            "/etc/resolv.conf to read. Every lookup will fail and every "
            "hop will be reported as unknown -- correct behaviour, but it "
            "means the diversity analysis can never confirm anything.",
            hint="Set IP_INTEL_DNS_RESOLVERS to the resolver this host "
            "should use. There is no public default on purpose: those "
            "queries are a list of your proxy inventory, and handing it to "
            "a third-party resolver would be the disclosure this product "
            "exists to prevent.",
            id="proxies.W008",
        )
    ]


@register(Tags.compatibility, deploy=True)
def check_provider_sync_has_a_token(app_configs, **kwargs):
    """The Webshare adapter needs an API token, and there is no fallback.

    A warning rather than an error, and `deploy=True`, for the same reason
    `proxies.W008` is: on a development machine without the token the
    feature simply does not run, which is harmless and correct, and a
    warning on every `check` forever is a warning everybody scrolls past.

    In a deployment it means the scheduled sync is failing every night
    with a non-zero exit nobody is watching, and the symptom -- inventory
    quietly decaying as addresses rotate -- takes weeks to become visible
    and does not look like a configuration problem when it does.

    The check deliberately reports only presence. It never logs the value,
    a prefix of it, or its length: the token lists every proxy credential
    on the account in one request, so `check` output, which is pasted into
    tickets and CI logs, is the last place any part of it should appear.
    """
    from django.conf import settings

    backend = str(getattr(settings, "PROVIDER_SYNC_BACKEND", ""))
    if not backend.endswith("WebshareSync"):
        return []

    if str(getattr(settings, "WEBSHARE_API_TOKEN", "") or "").strip():
        return []

    return [
        Warning(
            "PROVIDER_SYNC_BACKEND is the Webshare adapter but "
            "WEBSHARE_API_TOKEN is not set. Every `sync_webshare` run will "
            "fail, the provider's current inventory will never be loaded, "
            "and the symptom -- addresses rotating away and never being "
            "replaced -- looks like proxies dying rather than like a "
            "missing variable.",
            hint="Set WEBSHARE_API_TOKEN in backend/.env. Treat it like "
            "PROXY_CRED_KEY: one request with it returns every proxy "
            "credential on the account, so it is never committed, never "
            "logged and never pasted into a ticket.",
            id="proxies.W009",
        )
    ]
