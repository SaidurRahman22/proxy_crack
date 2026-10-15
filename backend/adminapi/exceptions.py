"""Admin-API exceptions, rendered through the existing error envelope.

`api.exceptions.api_exception_handler` wraps every DRF exception as

    {"error": {"code": ..., "status": ..., "detail": ...}}

DRF coerces each leaf of `detail` to a string, which would turn the
subscription counts the SPA renders into `"3"`. These classes re-assign
`self.detail` after `super().__init__` -- the same workaround
`api.exceptions.EntitlementDenied` uses -- so numbers stay numbers.
"""

from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied

__all__ = [
    "AdminLoginFailed",
    "CannotDeleteSelf",
    "Conflict",
    "PlanHasActiveSubscriptions",
    "PlanInUse",
    "PrivilegeEscalationDenied",
    "ProxyInUse",
    "SetPasswordDenied",
]


class _StructuredDetailMixin:
    def __init__(self, detail=None, code=None):
        super().__init__(detail=detail, code=code)
        if isinstance(detail, dict):
            self.detail = detail


class AdminLoginFailed(PermissionDenied):
    """Every admin login failure, whatever caused it.

    Bad password, unknown address and correct-password-but-not-staff all
    return this identical 403. Splitting them -- 401 for unknown, 403 for
    non-staff -- would turn the login form into an oracle that confirms
    which addresses have accounts and which passwords are right, which is
    exactly what the contract forbids.
    """

    default_detail = "Invalid credentials, or this account is not a staff account."
    default_code = "admin_login_failed"


class PrivilegeEscalationDenied(_StructuredDetailMixin, PermissionDenied):
    """A staff account tried to grant privileges it does not have, or to
    change its own.

    The contract treats `is_staff` and `is_superuser` as ordinary fields,
    which would make every staff account one PATCH away from Django Admin.
    This is the boundary *between* staff -- the only one the design has.
    """

    default_detail = (
        "Only a superuser may change privilege flags, and never their own."
    )
    default_code = "privilege_change_forbidden"


class SetPasswordDenied(_StructuredDetailMixin, PermissionDenied):
    """Resetting a peer's or a superuser's password is account takeover.

    It is a quieter path to the same place as `is_superuser`: set the
    superuser's password, then sign in at `/admin/`, which is a broader
    write surface than this API.
    """

    default_detail = "You are not allowed to set this user's password."
    default_code = "set_password_forbidden"


class Conflict(_StructuredDetailMixin, APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The request conflicts with the current state."
    default_code = "conflict"


class PlanHasActiveSubscriptions(Conflict):
    default_detail = "This plan has active subscriptions."
    default_code = "plan_has_active_subscriptions"


class PlanInUse(Conflict):
    """Referenced by subscription rows that are not active.

    `Subscription.plan` is `on_delete=PROTECT`, so history alone blocks the
    delete. Without this the database raises `ProtectedError` and the SPA
    gets a 500 for a condition the operator can reason about.
    """

    default_detail = "This plan is referenced by existing subscriptions."
    default_code = "plan_in_use"


class CannotDeleteSelf(Conflict):
    """Beyond the contract, and deliberate: deleting the account whose token
    is making the request cannot be undone and is never the intent."""

    default_detail = "You cannot delete the account you are signed in as."
    default_code = "cannot_delete_self"


class ProxyInUse(Conflict):
    """`ChainHop.proxy` is `on_delete=PROTECT`: a proxy inside a saved user
    chain cannot be deleted without destroying that chain."""

    default_detail = "This proxy is used by one or more saved chains."
    default_code = "proxy_in_use"
