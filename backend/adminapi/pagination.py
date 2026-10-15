"""Admin pagination: `?page=` / `?page_size=`, default 25, hard max 200.

Separate from `api.pagination.StandardResultsPagination` (max 100) because
the admin tables are dense and the two ceilings should be free to diverge
without one API's tuning changing the other's.
"""

from rest_framework.pagination import PageNumberPagination


class AdminPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 200
