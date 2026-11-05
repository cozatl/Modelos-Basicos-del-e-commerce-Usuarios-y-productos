from rest_framework import serializers

from addresses.models import Address
from billing.models import Payment
from products.models import Product

from .models import Cart, CartItem, Order, OrderItem, purchase_problem


class CartProductSerializer(serializers.ModelSerializer):
    """The few product fields the cart needs to render a row."""

    image = serializers.ImageField(read_only=True)

    class Meta:
        model = Product
        fields = ("id", "slug", "name", "image", "product_type", "stock")
        read_only_fields = fields


class CartItemSerializer(serializers.ModelSerializer):
    """Read shape of a cart row. Prices come from the product, never from the client."""

    product = CartProductSerializer(read_only=True)
    unit_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True, allow_null=True
    )
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    max_quantity = serializers.IntegerField(read_only=True)
    # Set when the product changed after it was added (deactivated, out of stock...).
    problem = serializers.CharField(read_only=True, allow_null=True)

    class Meta:
        model = CartItem
        fields = (
            "id",
            "product",
            "quantity",
            "unit_price",
            "subtotal",
            "max_quantity",
            "problem",
        )
        read_only_fields = fields


class CartSerializer(serializers.ModelSerializer):
    """Full cart returned by every /cart/ endpoint. The owner always comes
    from request.user."""

    items = CartItemSerializer(many=True, read_only=True)
    total_quantity = serializers.IntegerField(read_only=True)
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    has_problems = serializers.BooleanField(read_only=True)

    class Meta:
        model = Cart
        fields = (
            "id",
            "items",
            "total_quantity",
            "total",
            "has_problems",
            "updated_at",
        )
        read_only_fields = fields


class CartItemCreateSerializer(serializers.Serializer):
    """POST /cart/items/ — adds `quantity` units to the cart
    (merged with an existing row)."""

    product = serializers.SlugRelatedField(
        slug_field="slug", queryset=Product.objects.all()
    )
    quantity = serializers.IntegerField(min_value=1, default=1)

    def validate(self, attrs):
        cart = self.context["cart"]
        product = attrs["product"]
        existing = cart.items.filter(product=product).first()
        final_quantity = attrs["quantity"] + (existing.quantity if existing else 0)
        problem = purchase_problem(product, final_quantity)
        if problem:
            raise serializers.ValidationError({"quantity": [problem]})
        attrs["existing"] = existing
        attrs["final_quantity"] = final_quantity
        return attrs


class CartItemUpdateSerializer(serializers.Serializer):
    """PATCH /cart/items/<id>/ — sets the quantity of one row
    (use DELETE to remove it)."""

    quantity = serializers.IntegerField(min_value=1)

    def validate(self, attrs):
        item = self.context["item"]
        problem = purchase_problem(item.product, attrs["quantity"])
        if problem:
            raise serializers.ValidationError({"quantity": [problem]})
        return attrs


MONEY = {"max_digits": 12, "decimal_places": 2, "read_only": True}


class OrderItemSerializer(serializers.ModelSerializer):
    product_slug = serializers.SlugRelatedField(
        source="product", slug_field="slug", read_only=True, allow_null=True
    )

    class Meta:
        model = OrderItem
        fields = (
            "id",
            "product_slug",
            "product_name",
            "sku",
            "product_type",
            "unit_price",
            "quantity",
            "subtotal",
        )
        read_only_fields = fields


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = (
            "reference",
            "provider",
            "status",
            "amount",
            "failure_reason",
            "created_at",
        )
        read_only_fields = fields


class OrderSerializer(serializers.ModelSerializer):
    """Read shape of an order. Everything is computed and stored by the server."""

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    subtotal_before_tax = serializers.DecimalField(**MONEY)
    shipping_address = serializers.SerializerMethodField()
    expires_in_seconds = serializers.IntegerField(read_only=True, allow_null=True)
    can_pay = serializers.BooleanField(read_only=True)
    can_cancel = serializers.BooleanField(read_only=True)
    can_request_cancellation = serializers.BooleanField(read_only=True)

    class Meta:
        model = Order
        fields = (
            "order_id",
            "status",
            "status_display",
            "currency",
            "items",
            "items_subtotal",
            "shipping_cost",
            "total",
            "tax_rate",
            "tax_amount",
            "subtotal_before_tax",
            "needs_shipping",
            "shipping_zone_name",
            "free_shipping_applied",
            "shipping_address",
            "tracking_number",
            "notes",
            "payments",
            "created_at",
            "expires_at",
            "expires_in_seconds",
            "paid_at",
            "shipped_at",
            "delivered_at",
            "cancelled_at",
            "cancel_requested_at",
            "can_pay",
            "can_cancel",
            "can_request_cancellation",
        )
        read_only_fields = fields

    def get_shipping_address(self, order):
        if not order.has_shipping_address:
            return None
        data = {field: getattr(order, field) for field in Address.SNAPSHOT_FIELDS}
        data["state_display"] = order.get_state_display()
        return data


class OrderListSerializer(serializers.ModelSerializer):
    """Lighter shape for GET /orders/ (no address nor payments)."""

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    item_count = serializers.SerializerMethodField()
    expires_in_seconds = serializers.IntegerField(read_only=True, allow_null=True)
    can_pay = serializers.BooleanField(read_only=True)

    class Meta:
        model = Order
        fields = (
            "order_id",
            "status",
            "status_display",
            "total",
            "currency",
            "item_count",
            "created_at",
            "expires_in_seconds",
            "can_pay",
            "cancel_requested_at",
        )
        read_only_fields = fields

    def get_item_count(self, order):
        return sum(item.quantity for item in order.items.all())


class UserAddressField(serializers.PrimaryKeyRelatedField):
    """Only the requesting user's addresses are valid choices."""

    def get_queryset(self):
        return Address.objects.filter(user=self.context["request"].user)


class OrderCreateSerializer(serializers.Serializer):
    """POST /orders/ — the cart becomes an order. `address` is required
    with physical products."""

    address = UserAddressField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=500)


class OrderPaySerializer(serializers.Serializer):
    """POST /orders/<order_id>/pay/ — simulated provider only: pick the outcome."""

    outcome = serializers.ChoiceField(
        choices=("approved", "declined"), default="approved"
    )


class OrderCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)


class CheckoutQuoteSerializer(serializers.Serializer):
    """Query of GET /checkout/quote/: an address id or a bare postal code."""

    address = UserAddressField(required=False, allow_null=True)
    postal_code = serializers.RegexField(r"^\d{5}$", required=False)


class CheckoutQuoteResultSerializer(serializers.Serializer):
    items_subtotal = serializers.DecimalField(**MONEY)
    needs_shipping = serializers.BooleanField()
    shipping_cost = serializers.DecimalField(**MONEY)
    shipping_zone_name = serializers.CharField()
    free_shipping_applied = serializers.BooleanField()
    total = serializers.DecimalField(**MONEY)
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=4)
    tax_amount = serializers.DecimalField(**MONEY)
    reservation_minutes = serializers.IntegerField()
