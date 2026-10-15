"""The single gate in front of the admin API.

The mobile API at `/api/` is entitlement-scoped: a leak there exposes one
user's slice. The admin API is deliberately unscoped -- every user, every
plan, every proxy credential -- so a missed permission class here is a total
compromise, not a partial one. That asymmetry is why this permission is a
named, reusable class with its own tests rather than an `is_staff` check
written out at each view, and why `adminapi.checks` refuses to let the
process start if a route under `/api/admin/` is missing it.
"""

from rest_framework.permissions import BasePermission

__all__ = ["IsStaffUser"]


class IsStaffUser(BasePermission):
    """Authenticated, active, and flagged as staff.

    `is_active` is checked explicitly. A JWT outlives the account it was
    minted for: deactivating a user does not revoke tokens already issued,
    so without this an access token held by a disabled staff member keeps
    working until it expires.
    """

    message = "Staff access is required."
    code = "staff_required"

    def _is_staff(self, request):
        user = getattr(request, "user", None)
        return bool(
            user is not None
            and user.is_authenticated
            and user.is_active
            and user.is_staff
        )

    def has_permission(self, request, view):
        return self._is_staff(request)

    def has_object_permission(self, request, view, obj):
        # Second layer. A view that forgets to call check_permissions still
        # cannot hand back an object.
        return self._is_staff(request)
