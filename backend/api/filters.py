"""Query filters for the proxy list.

These narrow what the entitlement layer has already allowed. They can never
widen it: `ProxyViewSet.get_queryset()` applies the entitlement scope first.
"""

import django_filters

from proxies.models import Proxy, ProxyProtocol, ProxyTier


class ProxyFilter(django_filters.FilterSet):
    country = django_filters.CharFilter(field_name="country", lookup_expr="iexact")
    city = django_filters.CharFilter(field_name="city", lookup_expr="iexact")
    protocol = django_filters.ChoiceFilter(choices=ProxyProtocol.choices)
    tier = django_filters.ChoiceFilter(choices=ProxyTier.choices)
    is_alive = django_filters.BooleanFilter(field_name="is_alive")
    max_latency_ms = django_filters.NumberFilter(
        field_name="latency_ms", lookup_expr="lte"
    )
    # Health filters. These mirror what a constrained chain hop can ask
    # for, so a user can browse exactly the set a hop would resolve from
    # before committing to the constraint.
    #
    # Each excludes unprobed proxies, because a null p95 or a null success
    # rate cannot satisfy a numeric floor or ceiling. That is the correct
    # reading of "p95 under 200 ms" -- there is no measurement to make the
    # promise with -- but it does mean these filters return nothing on an
    # inventory that has never been probed.
    max_p95_latency_ms = django_filters.NumberFilter(
        field_name="latency_p95_ms", lookup_expr="lte"
    )
    min_success_rate = django_filters.NumberFilter(
        field_name="success_rate", lookup_expr="gte"
    )
    min_quality_score = django_filters.NumberFilter(
        field_name="quality_score", lookup_expr="gte"
    )

    class Meta:
        model = Proxy
        fields = [
            "country",
            "city",
            "protocol",
            "tier",
            "is_alive",
            "max_latency_ms",
            "max_p95_latency_ms",
            "min_success_rate",
            "min_quality_score",
        ]
