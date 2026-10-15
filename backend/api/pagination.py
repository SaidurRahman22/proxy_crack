"""Pagination. Proxy inventory is never returned unpaginated."""

from rest_framework.pagination import PageNumberPagination


class StandardResultsPagination(PageNumberPagination):
    page_size_query_param = "page_size"
    max_page_size = 100
