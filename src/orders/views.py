from rest_framework import permissions, viewsets

from .models import Cart, Order
from .serializers import CartSerializer, OrderSerializer


class CartViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows shopping carts to be viewed, created,
    updated, or deleted.
    """

    queryset = Cart.objects.all().order_by("-id")
    serializer_class = CartSerializer
    permission_classes = (permissions.IsAuthenticatedOrReadOnly,)

    def get_queryset(self):
        queryset = Cart.objects.all().order_by("-id")
        if self.request.user.is_authenticated:
            return queryset.filter(user=self.request.user)
        return queryset.none()


class OrderViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows orders to be viewed, created, updated, or deleted.
    """

    queryset = Order.objects.all().order_by("-created_at")
    serializer_class = OrderSerializer
    permission_classes = (permissions.IsAuthenticatedOrReadOnly,)

    def get_queryset(self):
        queryset = Order.objects.all().order_by("-created_at")
        if self.request.user.is_authenticated:
            return queryset.filter(cart__user=self.request.user)
        return queryset.none()
