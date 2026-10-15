"""Admin authentication.

The mobile API's login is `api.serializers.EmailTokenObtainPairSerializer`
and is deliberately untouched. This one differs in exactly one way that
matters: **staff is checked before a token is minted**, and every failure
mode returns the same 403.

Why one status code for everything: the contract requires that a correct
password for a non-staff account be refused with 403 and that the endpoint
not reveal whether an account exists. Returning 401 for "no such account"
and 403 for "not staff" would satisfy the letter of the first rule while
breaking the second -- the pair of status codes is an oracle that confirms
both the address and the password. So bad password, unknown address and
valid-but-not-staff are indistinguishable: same status, same body.
"""

import logging

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import update_last_login
from rest_framework import serializers
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenRefreshSerializer,
)
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken

from adminapi.audit import audit
from adminapi.authentication import has_admin_scope, stamp_admin_scope
from adminapi.exceptions import AdminLoginFailed
from adminapi.serializers import AdminMeSerializer

logger = logging.getLogger("proxycrack.adminapi")

User = get_user_model()


class AdminTokenObtainPairSerializer(TokenObtainPairSerializer):
    """`POST /api/admin/auth/login/` -> {access, refresh, user}."""

    @classmethod
    def get_token(cls, user):
        """Stamp the admin scope claim. This endpoint is the only place a
        token carrying it is ever minted -- see `adminapi.authentication`."""
        return stamp_admin_scope(super().get_token(user))

    def validate(self, attrs):
        # Deliberately not calling super().validate(): it mints the token
        # pair and fires UPDATE_LAST_LOGIN as part of authenticating, which
        # would record a successful login for a non-staff account that is
        # about to be refused.
        credentials = {
            self.username_field: (attrs.get(self.username_field) or "").strip().lower(),
            "password": attrs.get("password") or "",
        }
        request = self.context.get("request")
        if request is not None:
            credentials["request"] = request

        user = authenticate(**credentials)

        # `authenticate()` already returns None for an inactive user via
        # ModelBackend.user_can_authenticate; is_active is re-checked anyway
        # so a custom backend cannot quietly widen this.
        if (
            user is None
            or not user.is_active
            or not user.is_staff
            or not jwt_settings.USER_AUTHENTICATION_RULE(user)
        ):
            audit(
                "admin_login_failed",
                request=request,
                level=logging.WARNING,
                email=credentials[self.username_field],
                # Enough to investigate, not enough to confirm an address
                # exists to anyone who only has the HTTP response.
                reason=(
                    "not_staff"
                    if user is not None and user.is_staff is False
                    else "bad_credentials"
                ),
            )
            raise AdminLoginFailed()

        self.user = user
        refresh = self.get_token(user)

        if jwt_settings.UPDATE_LAST_LOGIN:
            update_last_login(None, user)

        audit("admin_login", request=request, target_user_id=user.pk)
        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
            "user": AdminMeSerializer(user).data,
        }


class AdminTokenRefreshSerializer(TokenRefreshSerializer):
    """`POST /api/admin/auth/refresh/` -> {access, refresh}.

    Re-checks staff on every refresh. An access token lives 30 minutes, so
    without this, revoking someone's staff flag would leave them with a
    self-renewing admin session for as long as their refresh token lasts.

    Returns a new `refresh` as well as `access` because the project runs
    with ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_ROTATION: the submitted
    refresh token is blacklisted by this call, so a client that keeps the
    old one is logged out at the next refresh.
    """

    def validate(self, attrs):
        try:
            token = RefreshToken(attrs["refresh"])
        except TokenError as exc:
            raise InvalidToken(exc.args[0])

        # Independent control #1: the token must have been minted by the
        # admin login endpoint. A staff member's *mobile* refresh token is
        # refused here, so the two sessions cannot be traded for each other.
        if not has_admin_scope(token):
            logger.warning("admin_refresh_denied reason=no_admin_scope")
            raise AdminLoginFailed()

        # Independent control #2: the subject is still staff and still
        # active. An access token lives 30 minutes; without this, revoking
        # someone's staff flag leaves them a self-renewing admin session for
        # as long as their refresh token lasts.
        user_id = token.payload.get(jwt_settings.USER_ID_CLAIM)
        user = User.objects.filter(**{jwt_settings.USER_ID_FIELD: user_id}).first()
        if user is None or not user.is_active or not user.is_staff:
            logger.warning("admin_refresh_denied user=%s reason=not_staff", user_id)
            raise AdminLoginFailed()

        return super().validate(attrs)


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()
