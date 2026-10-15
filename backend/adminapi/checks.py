"""Startup check: no route under /api/admin/ is reachable without staff.

The admin API is unscoped -- every user row, every subscription, every
decrypted proxy credential. A viewset added later that forgets to inherit
`AdminViewMixin` would silently fall back to the project-wide
`DEFAULT_PERMISSION_CLASSES` of `IsAuthenticated`, and any mobile user's
token would read the whole database. That is a one-line mistake with total
consequences, and code review is not a reliable control for it.

So it is checked mechanically. `manage.py check` runs before `runserver`,
`migrate` and the test runner, which makes a missing permission class a
process that will not start rather than a leak nobody notices.

Two endpoints cannot require a token and are exempted **by name**, not by
pattern: login (there is no token yet) and refresh (the access token being
renewed is expired by definition). Both re-check `is_staff` inside their
serializer. Adding to `TOKENLESS_ENDPOINTS` is a deliberate, visible act.
"""

from django.core.checks import Error, Tags, register
from django.urls import URLPattern, URLResolver

# url names, relative to the `adminapi` namespace.
TOKENLESS_ENDPOINTS = {
    "auth-login",
    "auth-refresh",
}


def _iter_patterns(patterns, prefix=""):
    for entry in patterns:
        route = prefix + str(entry.pattern)
        if isinstance(entry, URLResolver):
            yield from _iter_patterns(entry.url_patterns, route)
        elif isinstance(entry, URLPattern):
            yield route, entry


def _permission_classes(callback):
    """The permission classes a route will actually instantiate.

    `as_view(**initkwargs)` can override a class attribute per route, so the
    initkwargs win where present -- reading `cls.permission_classes` alone
    would miss that.
    """
    view_class = getattr(callback, "cls", None)
    if view_class is None:
        return None
    initkwargs = getattr(callback, "initkwargs", None) or {}
    return tuple(
        initkwargs.get("permission_classes", getattr(view_class, "permission_classes", ()))
    )


def audit_patterns(urlpatterns):
    """The check's body, taking the patterns as an argument so the test
    suite can point it at a deliberately-unguarded view and prove it
    actually fails."""
    from rest_framework.permissions import IsAuthenticated

    from adminapi.permissions import IsStaffUser

    errors = []
    seen = 0

    for route, pattern in _iter_patterns(urlpatterns):
        name = pattern.name
        callback = pattern.callback
        view_class = getattr(callback, "cls", None)

        if view_class is None:
            errors.append(
                Error(
                    f"/api/admin/{route} is not a DRF view, so its permissions "
                    f"cannot be verified.",
                    hint="Every admin route must be an APIView or ViewSet.",
                    id="adminapi.E003",
                )
            )
            continue

        if name in TOKENLESS_ENDPOINTS:
            seen += 1
            continue

        permissions = _permission_classes(callback)
        seen += 1

        if IsStaffUser not in permissions:
            errors.append(
                Error(
                    f"/api/admin/{route} ({view_class.__name__}) does not "
                    f"require IsStaffUser. The admin API is unscoped: an "
                    f"endpoint without this permission hands the entire "
                    f"database to any authenticated mobile user.",
                    hint=(
                        "Inherit adminapi.views.AdminViewMixin (or one of "
                        "AdminModelViewSet / AdminReadOnlyViewSet), or add "
                        "the url name to adminapi.checks.TOKENLESS_ENDPOINTS "
                        "if it genuinely must be reachable without a staff "
                        "token."
                    ),
                    id="adminapi.E001",
                )
            )

        if IsAuthenticated not in permissions:
            errors.append(
                Error(
                    f"/api/admin/{route} ({view_class.__name__}) does not "
                    f"require IsAuthenticated.",
                    hint="Add IsAuthenticated alongside IsStaffUser.",
                    id="adminapi.E002",
                )
            )

    if not seen:
        errors.append(
            Error(
                "No routes were found under /api/admin/. The staff-only "
                "check is therefore verifying nothing.",
                id="adminapi.E004",
            )
        )

    return errors


@register(Tags.security)
def check_admin_endpoints_require_staff(app_configs, **kwargs):
    from adminapi.urls import urlpatterns

    return audit_patterns(urlpatterns)
