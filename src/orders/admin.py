from django.contrib import admin

from .models import Cart, CartItem, Order


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    raw_id_fields = ("product",)
    readonly_fields = ("unit_price", "subtotal", "problem", "added_at")


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "total_quantity", "total", "updated_at")
    search_fields = ("user__email",)
    raw_id_fields = ("user",)
    readonly_fields = ("total_quantity", "total", "created_at", "updated_at")
    inlines = (CartItemInline,)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("items__product")


admin.site.register(Order)
