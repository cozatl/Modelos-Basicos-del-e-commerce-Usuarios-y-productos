from rest_framework import serializers

from .models import Cart, Order


class CartSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cart
        fields = ("id", "user", "products")


class OrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = ("id", "cart", "order_id", "total", "created_at")
        read_only_fields = ("order_id", "created_at")
