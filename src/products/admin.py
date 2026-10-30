from django.contrib import admin

from .models import Category, Product


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "parent", "slug", "is_active")
    list_filter = ("is_active", "parent")
    list_editable = ("is_active",)
    search_fields = ("name",)
    readonly_fields = ("slug",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "category",
        "product_type",
        "access_type",
        "price",
        "stock",
        "is_featured",
        "is_active",
    )
    list_select_related = ("category",)
    list_filter = (
        "category",
        "product_type",
        "access_type",
        "is_featured",
        "is_active",
    )
    list_editable = ("is_featured", "is_active")
    search_fields = ("name", "sku", "description")
    readonly_fields = ("id", "slug", "created_at", "updated_at")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "id",
                    "name",
                    "slug",
                    "category",
                    "description",
                    "image",
                )
            },
        ),
        ("Tipo", {"fields": ("product_type", "access_type")}),
        ("Precio", {"fields": ("price", "discount_price")}),
        (
            "Inventario (solo físicos)",
            {"fields": ("sku", "stock", "weight")},
        ),
        ("Visibilidad", {"fields": ("is_active", "is_featured")}),
        ("Fechas", {"fields": ("created_at", "updated_at")}),
    )
