from rest_framework.pagination import PageNumberPagination


class ProductPagination(PageNumberPagination):
    page_size = 3  # Number of items per page
    page_size_query_param = "page_size"  # Allow clients to set the page size
    page_query_param = "page"  # Allow clients to set the page name
    max_page_size = 10  # Maximum number of items per page
    # Allow clients to use any string to get the last page
    last_page_strings = ("last",)
