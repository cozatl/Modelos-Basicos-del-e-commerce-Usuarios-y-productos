from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Cart, CartItem, Order
from .serializers import (
    CartItemCreateSerializer,
    CartItemUpdateSerializer,
    CartSerializer,
    OrderSerializer,
)


def cart_response(request, cart, status_code=status.HTTP_200_OK):
    """Every cart endpoint answers with the whole cart, totals computed on the server."""
    cart = Cart.objects.prefetch_related("items__product").get(pk=cart.pk)
    data = CartSerializer(cart, context={"request": request}).data
    return Response(data, status=status_code)


def locked_cart_for(user):
    """
    The user's cart, row-locked until the surrounding transaction ends, so two quick
    requests (double click, two tabs) cannot both pass the stock check.
    Call inside transaction.atomic().
    """
    cart = Cart.for_user(user)
    return Cart.objects.select_for_update().get(pk=cart.pk)


class CartView(APIView):
    """
    GET    /cart/  — the current user's cart (created on the fly if missing).
    DELETE /cart/  — empties it.
    """

    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        return cart_response(request, Cart.for_user(request.user))

    def delete(self, request):
        with transaction.atomic():
            cart = locked_cart_for(request.user)
            cart.items.all().delete()
        return cart_response(request, cart)


class CartItemCreateView(APIView):
    """POST /cart/items/ {product: <slug>, quantity?: int} — adds to (or merges into) the cart."""

    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request):
        with transaction.atomic():
            cart = locked_cart_for(request.user)
            serializer = CartItemCreateSerializer(
                data=request.data, context={"cart": cart, "request": request}
            )
            serializer.is_valid(raise_exception=True)
            existing = serializer.validated_data["existing"]
            quantity = serializer.validated_data["final_quantity"]
            if existing:
                existing.quantity = quantity
                existing.save(update_fields=("quantity", "updated_at"))
                status_code = status.HTTP_200_OK
            else:
                CartItem.objects.create(
                    cart=cart,
                    product=serializer.validated_data["product"],
                    quantity=quantity,
                )
                status_code = status.HTTP_201_CREATED
            cart.save(update_fields=("updated_at",))
        return cart_response(request, cart, status_code)


class CartItemDetailView(APIView):
    """
    PATCH  /cart/items/<id>/ {quantity: int} — sets the quantity of one row.
    DELETE /cart/items/<id>/                 — removes the row.
    Rows of other users' carts answer 404.
    """

    permission_classes = (permissions.IsAuthenticated,)

    def patch(self, request, pk):
        with transaction.atomic():
            cart = locked_cart_for(request.user)
            item = get_object_or_404(
                CartItem.objects.select_related("product"), pk=pk, cart=cart
            )
            serializer = CartItemUpdateSerializer(
                data=request.data, context={"item": item, "request": request}
            )
            serializer.is_valid(raise_exception=True)
            item.quantity = serializer.validated_data["quantity"]
            item.save(update_fields=("quantity", "updated_at"))
            cart.save(update_fields=("updated_at",))
        return cart_response(request, cart)

    def delete(self, request, pk):
        with transaction.atomic():
            cart = locked_cart_for(request.user)
            item = get_object_or_404(CartItem, pk=pk, cart=cart)
            item.delete()
            cart.save(update_fields=("updated_at",))
        return cart_response(request, cart)


class OrderViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only until M4. It used to accept POST with any `cart` id, so a user could
    create an order from someone else's cart; orders will be created by checkout in M4.
    """

    queryset = Order.objects.none()  # lets the router derive the basename; see get_queryset
    serializer_class = OrderSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_queryset(self):
        return Order.objects.filter(cart__user=self.request.user).order_by(
            "-created_at"
        )
