"""Stable error envelope and the entitlement-specific exceptions.

Every error response has the same shape so the Flutter client can branch on
`error.code` rather than on a human-readable string:

    {"error": {"code": "chain_length_exceeded", "status": 403, "detail": ...}}
"""

from rest_framework.exceptions import PermissionDenied
from rest_framework.views import exception_handler as drf_exception_handler


class EntitlementDenied(PermissionDenied):
    """A 403 that can carry a structured detail.

    DRF coerces every leaf of an exception detail to a string. The limits in
    these errors are numbers the client compares against, so the raw dict is
    kept instead.
    """

    def __init__(self, detail=None, code=None):
        super().__init__(detail=detail, code=code)
        if isinstance(detail, dict):
            self.detail = detail


class ChainLengthExceeded(EntitlementDenied):
    default_detail = "Your plan does not allow a chain this long."
    default_code = "chain_length_exceeded"


class ProxyNotEntitled(EntitlementDenied):
    default_detail = "Your plan does not include one or more of these proxies."
    default_code = "proxy_not_entitled"


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    code = getattr(exc, "default_code", None) or "error"
    detail = response.data

    # DRF puts the real code in the detail object for simple errors.
    if isinstance(detail, dict) and "detail" in detail and len(detail) == 1:
        inner = detail["detail"]
        code = getattr(inner, "code", code)
        detail = str(inner)

    response.data = {
        "error": {
            "code": code,
            "status": response.status_code,
            "detail": detail,
        }
    }
    return response
