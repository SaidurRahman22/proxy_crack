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

    class Meta:
        model = Proxy
        fields = ["country", "city", "protocol", "tier", "is_alive", "max_latency_ms"]
