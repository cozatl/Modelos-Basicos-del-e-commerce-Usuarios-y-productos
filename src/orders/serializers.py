from rest_framework import serializers

from products.models import Product

from .models import Cart, CartItem, Order, purchase_problem


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
    """Full cart returned by every /cart/ endpoint. The owner always comes from request.user."""

    items = CartItemSerializer(many=True, read_only=True)
    total_quantity = serializers.IntegerField(read_only=True)
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    has_problems = serializers.BooleanField(read_only=True)

    class Meta:
        model = Cart
        fields = ("id", "items", "total_quantity", "total", "has_problems", "updated_at")
        read_only_fields = fields


class CartItemCreateSerializer(serializers.Serializer):
    """POST /cart/items/ — adds `quantity` units to the cart (merged with an existing row)."""

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
    """PATCH /cart/items/<id>/ — sets the quantity of one row (use DELETE to remove it)."""

    quantity = serializers.IntegerField(min_value=1)

    def validate(self, attrs):
        item = self.context["item"]
        problem = purchase_problem(item.product, attrs["quantity"])
        if problem:
            raise serializers.ValidationError({"quantity": [problem]})
        return attrs


class OrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = ("id", "cart", "order_id", "total", "created_at")
        read_only_fields = fields
