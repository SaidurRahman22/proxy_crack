"""Object-level ownership check.

The chain queryset is already filtered to `request.user`; this is the second
layer, so a future queryset change cannot silently expose another user's
presets.
"""

from rest_framework.permissions import BasePermission


class IsOwner(BasePermission):
    message = "This object does not belong to you."

    def has_object_permission(self, request, view, obj):
        owner_id = getattr(obj, "user_id", None)
        return owner_id is not None and owner_id == request.user.pk
