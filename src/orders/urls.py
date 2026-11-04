"""
Cart (M3) and checkout quote (M4) endpoints. Orders and addresses use the router.
Include them under the same prefix as the rest of the API:

    path("api/v1/", include("orders.urls")),

The trailing slash is optional so /cart and /cart/ both work with PATCH/DELETE
(APPEND_SLASH can only redirect GETs).
"""

from django.urls import re_path

from .views import (
    CartItemCreateView,
    CartItemDetailView,
    CartView,
    CheckoutQuoteView,
)

app_name = "orders"

urlpatterns = [
    re_path(r"^cart/?$", CartView.as_view(), name="cart"),
    re_path(r"^cart/items/?$", CartItemCreateView.as_view(), name="cart-items"),
    re_path(
        r"^cart/items/(?P<pk>\d+)/?$",
        CartItemDetailView.as_view(),
        name="cart-item-detail",
    ),
    # M4: totals of the cart (shipping + VAT) before placing the order
    re_path(
        r"^checkout/quote/?$",
        CheckoutQuoteView.as_view(),
        name="checkout-quote",
    ),
]
