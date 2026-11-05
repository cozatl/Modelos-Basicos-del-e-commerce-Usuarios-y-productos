from rest_framework import serializers

from .models import Category, Product
from .rules import product_rule_errors

RULE_FIELDS = (
    "product_type",
    "access_type",
    "price",
    "discount_price",
    "stock",
    "weight",
)


class CategorySerializer(serializers.ModelSerializer):
    parent = serializers.SlugRelatedField(
        slug_field="slug",
        queryset=Category.objects.filter(parent__isnull=True),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = Category
        fields = ("id", "name", "slug", "parent", "is_active")
        read_only_fields = ("slug",)

    def validate(self, attrs):
        parent = attrs.get("parent", getattr(self.instance, "parent", None))
        if self.instance and parent and self.instance.children.exists():
            raise serializers.ValidationError(
                {"parent": "A category with subcategories can't become one."}
            )
        if self.instance and parent and parent.pk == self.instance.pk:
            raise serializers.ValidationError(
                {"parent": "A category can't be its own parent."}
            )
        return attrs


class CategoryBriefSerializer(serializers.ModelSerializer):
    """Nested inside products (read only)."""

    class Meta:
        model = Category
        fields = ("id", "name", "slug")
        read_only_fields = fields


class ProductSerializer(serializers.ModelSerializer):
    """Full product: authenticated catalog and staff management."""

    # Read: {id, name, slug}. Write: category_slug="celulares" (or null)
    category = CategoryBriefSerializer(read_only=True)
    category_slug = serializers.SlugRelatedField(
        source="category",
        slug_field="slug",
        queryset=Category.objects.all(),
        allow_null=True,
        required=False,
        write_only=True,
    )
    is_physical = serializers.BooleanField(read_only=True)
    is_available = serializers.BooleanField(read_only=True)

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "slug",
            "description",
            "category",
            "category_slug",
            "product_type",
            "access_type",
            "price",
            "discount_price",
            "sku",
            "stock",
            "weight",
            "image",
            "is_active",
            "is_featured",
            "is_physical",
            "is_available",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("slug", "created_at", "updated_at")

    def validate(self, attrs):
        # On create, fall back to the model defaults (Product() has them);
        # on update (PATCH included), fall back to the stored values.
        base = self.instance or Product()
        data = {f: attrs.get(f, getattr(base, f)) for f in RULE_FIELDS}
        errors = product_rule_errors(**data)
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class ProductPreviewSerializer(serializers.ModelSerializer):
    """Public card for the home page: no description, stock or SKU."""

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "slug",
            "image",
            "category",
            "product_type",
            "access_type",
            "price",
            "discount_price",
        )
        read_only_fields = fields

    category = CategoryBriefSerializer(read_only=True)
