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
