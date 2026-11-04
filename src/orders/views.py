from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.payments import PaymentsDisabled

from . import services
from .models import Cart, CartItem, Order
from .serializers import (
    CartItemCreateSerializer,
    CartItemUpdateSerializer,
    CartSerializer,
    CheckoutQuoteResultSerializer,
    CheckoutQuoteSerializer,
    OrderCancelSerializer,
    OrderCreateSerializer,
    OrderListSerializer,
    OrderPaySerializer,
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
    """POST /cart/items/ {product: <slug>, quantity?: int} — adds to
    (or merges into) the cart."""

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


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"


class ServiceUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "unavailable"


CONFLICT_CODES = {"expired", "not_payable", "conflict"}


def as_api_error(exc):
    """services.CheckoutError -> DRF exception
    (409 for state conflicts, 400 otherwise)."""
    if exc.code in CONFLICT_CODES:
        return Conflict(detail={"detail": exc.message, "code": exc.code})
    body = {exc.field or "non_field_errors": [exc.message]}
    if exc.details:
        body["problems"] = exc.details
    return ValidationError(body)


class CheckoutQuoteView(APIView):
    """
    GET /checkout/quote/?address=<id>   or   ?postal_code=01000
    Totals of the current cart (shipping, VAT included) without creating anything.
    """

    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        query = CheckoutQuoteSerializer(
            data=request.query_params, context={"request": request}
        )
        query.is_valid(raise_exception=True)
        address = query.validated_data.get("address")
        postal_code = (
            address.postal_code if address else query.validated_data.get("postal_code")
        )
        try:
            totals = services.quote_cart(request.user, postal_code)
        except services.CheckoutError as exc:
            raise as_api_error(exc) from exc
        data = {
            **totals.__dict__,
            "reservation_minutes": services.reservation_minutes(),
        }
        return Response(CheckoutQuoteResultSerializer(data).data)


class OrderPagination(PageNumberPagination):
    page_size = 10


class OrderViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET  /orders/                      my orders (newest first, 10 per page)
    POST /orders/  {address?, notes?}  the cart becomes a pending order; stock is
                                       reserved for ORDER_RESERVATION_MINUTES
    GET  /orders/<order_id>/
    POST /orders/<order_id>/pay/       {outcome?: "approved"|"declined"} (simulated)
    POST /orders/<order_id>/cancel/    {reason?} — unpaid: cancelled now;
                                       paid: cancellation request for staff

    Other users' orders answer 404. Status changes by staff happen in the admin.
    """

    queryset = Order.objects.none()  # lets the router derive the basename
    permission_classes = (permissions.IsAuthenticated,)
    lookup_field = "order_id"
    pagination_class = OrderPagination

    def get_queryset(self):
        qs = Order.objects.filter(user=self.request.user)
        if self.action == "list":
            return qs.prefetch_related("items")
        return qs.prefetch_related("items__product", "payments")

    def get_serializer_class(self):
        if self.action == "list":
            return OrderListSerializer
        return OrderSerializer

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        # Safety net if Celery beat is not running: never show a stale reservation.
        if request.user.is_authenticated:
            services.expire_user_overdue_orders(request.user)

    def detail_response(self, order, status_code=status.HTTP_200_OK):
        order = self.get_queryset().get(pk=order.pk)
        return Response(
            OrderSerializer(order, context=self.get_serializer_context()).data,
            status=status_code,
        )

    def create(self, request, *args, **kwargs):
        payload = OrderCreateSerializer(data=request.data, context={"request": request})
        payload.is_valid(raise_exception=True)
        try:
            order = services.create_order_from_cart(
                request.user,
                address=payload.validated_data.get("address"),
                notes=payload.validated_data.get("notes", ""),
            )
        except services.CheckoutError as exc:
            raise as_api_error(exc) from exc
        return self.detail_response(order, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def pay(self, request, order_id=None):
        order = self.get_object()
        payload = OrderPaySerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            order, payment = services.pay_order(order, payload.validated_data["outcome"])
        except services.CheckoutError as exc:
            raise as_api_error(exc) from exc
        except PaymentsDisabled as exc:
            raise ServiceUnavailable(str(exc)) from exc
        if payment.status != "approved":
            raise ValidationError(
                {"payment": [payment.failure_reason or "The payment was declined."]}
            )
        return self.detail_response(order)

    @action(detail=True, methods=["post"])
    def cancel(self, request, order_id=None):
        order = self.get_object()
        payload = OrderCancelSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            order = services.customer_cancel(
                order, payload.validated_data.get("reason", "")
            )
        except services.CheckoutError as exc:
            raise as_api_error(exc) from exc
        return self.detail_response(order)
