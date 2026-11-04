from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Read-only: payments are written by the checkout and by order actions."""

    list_display = (
        "reference",
        "order",
        "provider",
        "status",
        "amount",
        "created_at",
    )
    list_filter = ("status", "provider")
    search_fields = ("reference", "order__order_id", "order__customer_email")
    readonly_fields = tuple(f.name for f in Payment._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
