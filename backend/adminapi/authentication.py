"""A second, independent control at the authentication layer.

The contract's Ground Rule 2 says a missed permission class on this API is a
total compromise. That is true as long as `IsStaffUser` is the *only* thing
separating a mobile token from the whole database: an end user's JWT is
signed with the same key, for the same user model, with no audience and no
role claim. It is cryptographically indistinguishable from a staff token.
One forgotten line and it is a staff token.

So admin tokens are minted with a `scope: "admin"` claim, by
`/api/admin/auth/login/` and nowhere else, and this authentication class
refuses anything without it -- **before** any permission class runs. A
token issued by the mobile `/api/auth/login/` is now useless here even if
its user is staff, and even if a view is missing `IsStaffUser`. Two
independent mistakes are now required instead of one.

The claim is carried onto refreshed access tokens automatically: SimpleJWT
copies every claim except `exp`/`jti`/`token_type` from the refresh token.

**Why this raises 403 and not 401.** The brief's hardest requirement is
that a valid end-user JWT receives 403, not data -- and a 401 would invite
the SPA's refresh-and-retry loop to spin on a token that can never work.
A malformed, expired or unsigned token still gets the usual 401 from the
parent class; only "valid token, wrong audience" is a 403.
"""

from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication

__all__ = [
    "ADMIN_SCOPE_CLAIM",
    "ADMIN_SCOPE_VALUE",
    "AdminJWTAuthentication",
    "AdminScopeRequired",
]

ADMIN_SCOPE_CLAIM = "scope"
ADMIN_SCOPE_VALUE = "admin"


class AdminScopeRequired(PermissionDenied):
    default_detail = (
        "This token was not issued for the admin API. Sign in at "
        "/api/admin/auth/login/."
    )
    default_code = "admin_scope_required"


def stamp_admin_scope(token):
    """Mark a freshly minted token as an admin token."""
    token[ADMIN_SCOPE_CLAIM] = ADMIN_SCOPE_VALUE
    return token


def has_admin_scope(token):
    return token.get(ADMIN_SCOPE_CLAIM) == ADMIN_SCOPE_VALUE


class AdminJWTAuthentication(JWTAuthentication):
    """JWT authentication that additionally requires the admin scope claim."""

    def get_validated_token(self, raw_token):
        token = super().get_validated_token(raw_token)
        if not has_admin_scope(token):
            raise AdminScopeRequired()
        return token
