"""Root URL configuration.

**Django Admin is deliberately not mounted.** D12 kept it at `/admin/` as a
superuser fallback; that is reversed. The React operations console talking
to `/api/admin/` is the only administrative surface.

The reason is not taste. Django Admin authenticates with a **session
cookie** while everything else in this project authenticates with a Bearer
token, so keeping it meant one ambient, browser-attached credential sitting
beside an API whose whole design assumes there is no such thing. That
single fact is what forced `CORS_ALLOW_CREDENTIALS = False` to be a
load-bearing control rather than an obvious default, what made
"`SessionAuthentication` must never be added" a rule somebody had to
remember, and what put `/admin/` at the end of every privilege-escalation
path in the threat model (T2/E-f: own the superuser's password, then sign
in somewhere with a broader write surface than the API itself). Unmounting
it deletes that whole class of concern instead of documenting it.

`adminapi` keeps its own startup check (`adminapi.E001`) over every route
under `/api/admin/`, so removing the fallback does not remove the
structural guarantee -- it concentrates it on one surface.
"""

from django.conf import settings
from django.urls import include, path

urlpatterns = [
    # Staff-only admin API. Listed before `api/` for readability only --
    # Django matches `api/admin/` against this entry first either way,
    # because `api.urls` has no `admin/` route to shadow it.
    path("api/admin/", include("adminapi.urls")),
    path("api/", include("api.urls")),
]

if settings.DEBUG:
    # Session login for the DRF browsable API during development only.
    # This is the one remaining thing in the project that can issue a
    # session cookie, and it is not mounted when DEBUG is off -- so in
    # production no cookie carries an identity at all.
    urlpatterns += [path("api-auth/", include("rest_framework.urls"))]
