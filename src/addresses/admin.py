from django.contrib import admin

from .models import Address, ShippingSettings, ShippingZone


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "label",
        "recipient_name",
        "postal_code",
        "municipality",
        "state",
        "is_default",
    )
    list_filter = ("state", "is_default")
    search_fields = ("user__email", "recipient_name", "postal_code", "street")
    raw_id_fields = ("user",)


@admin.register(ShippingZone)
class ShippingZoneAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "cost",
        "postal_code_count",
        "allows_free_shipping",
        "is_active",
        "sort_order",
    )
    list_editable = ("sort_order",)
    list_filter = ("is_active", "allows_free_shipping")
    search_fields = ("name", "postal_codes")  # find which zone has a CP

    @admin.display(description="Postal codes")
    def postal_code_count(self, obj):
        return len(obj.postal_code_list)


@admin.register(ShippingSettings)
class ShippingSettingsAdmin(admin.ModelAdmin):
    """Singleton: one row, created on demand, never deleted."""

    list_display = (
        "__str__",
        "default_zone",
        "free_shipping_threshold",
        "free_shipping_only_marked_zones",
    )

    def has_add_permission(self, request):
        return (
            super().has_add_permission(request) and not ShippingSettings.objects.exists()
        )

    def has_delete_permission(self, request, obj=None):
        return False
