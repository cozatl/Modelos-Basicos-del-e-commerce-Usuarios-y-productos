from django.contrib import admin, messages

from analytics.commissions import assign_seller
from billing.models import Payment

from . import services
from .choices import OrderStatus
from .models import Cart, CartItem, Order, OrderItem


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


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    fields = (
        "product_name",
        "sku",
        "product_type",
        "unit_price",
        "quantity",
        "subtotal",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    can_delete = False
    fields = (
        "reference",
        "provider",
        "status",
        "amount",
        "failure_reason",
        "created_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class CancelRequestFilter(admin.SimpleListFilter):
    title = "cancellation request"
    parameter_name = "cancel_requested"

    def lookups(self, request, model_admin):
        return (("yes", "Pending decision"),)

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(cancel_requested_at__isnull=False).exclude(
                status__in=(OrderStatus.REFUNDED, OrderStatus.CANCELLED)
            )
        return queryset


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """
    Status is never edited by hand: it changes through the actions below, which
    validate the transition and move the stock. Each action needs a permission:

    - "Can mark orders as shipped or delivered"      -> ship / deliver
    - "Can approve or reject cancellations and refunds" ->
       cancel / refund / reject

    Run `python manage.py setup_order_groups` to create ready-made
    staff groups.
    """

    list_display = (
        "order_id",
        "customer_email",
        "status",
        "channel",
        "location",
        "total",
        "needs_shipping",
        "created_at",
        "paid_at",
        "cancel_pending",
    )
    list_filter = (
        "status",
        "channel",
        "location",
        CancelRequestFilter,
        "needs_shipping",
        "created_at",
    )
    search_fields = (
        "order_id",
        "customer_email",
        "user__email",
        "postal_code",
    )
    date_hierarchy = "created_at"
    autocomplete_fields = ("sold_by",)
    inlines = (OrderItemInline, PaymentInline)
    actions = (
        "action_mark_shipped",
        "action_mark_delivered",
        "action_refund_restock",
        "action_refund_no_restock",
        "action_reject_cancellation",
        "action_recalculate_commission",
    )
    # Only these can be typed in; everything else is history.
    editable_fields = ("tracking_number", "staff_notes")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "order_id",
                    "user",
                    "customer_email",
                    "status",
                    "notes",
                )
            },
        ),
        (
            "Sale",
            {
                "fields": (
                    "channel",
                    "location",
                    "sold_by",
                    "commission_percent",
                    "commission_amount",
                )
            },
        ),
        (
            "Amounts (VAT included)",
            {
                "fields": (
                    "items_subtotal",
                    "shipping_cost",
                    "total",
                    "tax_rate",
                    "tax_amount",
                    "currency",
                )
            },
        ),
        (
            "Shipping",
            {
                "fields": (
                    "needs_shipping",
                    "shipping_zone_name",
                    "free_shipping_applied",
                    "recipient_name",
                    "phone",
                    "street",
                    "exterior_number",
                    "interior_number",
                    "neighborhood",
                    "postal_code",
                    "municipality",
                    "city",
                    "state",
                    "references",
                    "tracking_number",
                )
            },
        ),
        (
            "Cancellation",
            {
                "fields": (
                    "cancel_requested_at",
                    "cancel_reason",
                    "stock_released",
                )
            },
        ),
        (
            "Dates",
            {
                "fields": (
                    "created_at",
                    "expires_at",
                    "paid_at",
                    "shipped_at",
                    "delivered_at",
                    "cancelled_at",
                )
            },
        ),
        ("Internal", {"fields": ("staff_notes",)}),
    )

    def get_readonly_fields(self, request, obj=None):
        editable = set(self.editable_fields)
        if self.has_assign_order_seller_permission(request):
            editable.add("sold_by")
        return [f.name for f in Order._meta.fields if f.name not in editable]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user", "location")

    def save_model(self, request, obj, form, change):
        # The seller goes through assign_seller so the commission is frozen with it.
        seller_changed = "sold_by" in form.changed_data
        seller = obj.sold_by
        if seller_changed and change:
            obj.sold_by = Order.objects.get(pk=obj.pk).sold_by
        super().save_model(request, obj, form, change)
        if seller_changed:
            assign_seller(obj, seller)
            self.message_user(
                request,
                f"Seller of {obj.order_id} set to {seller or '—'}.",
                messages.SUCCESS,
            )

    @admin.display(boolean=True, description="Cancel requested")
    def cancel_pending(self, obj):
        return bool(obj.cancel_requested_at) and obj.status not in (
            OrderStatus.REFUNDED,
            OrderStatus.CANCELLED,
        )

    # Orders are only created by the checkout. Deleting is
    # for superusers (test data).
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    # Custom permissions used by the actions' allowed_permissions
    def has_change_order_status_permission(self, request):
        return request.user.has_perm("orders.change_order_status")

    def has_approve_order_cancellation_permission(self, request):
        return request.user.has_perm("orders.approve_order_cancellation")

    def has_assign_order_seller_permission(self, request):
        return request.user.has_perm("orders.assign_order_seller")

    def _run(self, request, queryset, func, done, **kwargs):
        ok = 0
        for order in queryset:
            try:
                func(order, staff_user=request.user, **kwargs)
                ok += 1
            except services.CheckoutError as exc:
                self.message_user(request, exc.message, messages.WARNING)
        if ok:
            self.message_user(request, f"{ok} order(s) {done}.", messages.SUCCESS)

    @admin.action(description="Mark as shipped", permissions=("change_order_status",))
    def action_mark_shipped(self, request, queryset):
        self._run(request, queryset, services.mark_shipped, "marked as shipped")

    @admin.action(description="Mark as delivered", permissions=("change_order_status",))
    def action_mark_delivered(self, request, queryset):
        self._run(request, queryset, services.mark_delivered, "marked as delivered")

    @admin.action(
        description="Approve cancellation / refund — return stock",
        permissions=("approve_order_cancellation",),
    )
    def action_refund_restock(self, request, queryset):
        self._run(
            request,
            queryset,
            services.approve_cancellation,
            "cancelled",
            restock=True,
        )

    @admin.action(
        description=("Approve refund — do NOT return stock (goods not recovered)"),
        permissions=("approve_order_cancellation",),
    )
    def action_refund_no_restock(self, request, queryset):
        self._run(
            request,
            queryset,
            services.approve_cancellation,
            "cancelled",
            restock=False,
        )

    @admin.action(
        description="Reject cancellation request",
        permissions=("approve_order_cancellation",),
    )
    def action_reject_cancellation(self, request, queryset):
        self._run(request, queryset, services.reject_cancellation, "updated")

    @admin.action(
        description="Recalculate commission with the seller's current percentage",
        permissions=("assign_order_seller",),
    )
    def action_recalculate_commission(self, request, queryset):
        ok = 0
        for order in queryset.filter(sold_by__isnull=False).select_related("sold_by"):
            assign_seller(order, order.sold_by)
            ok += 1
        self.message_user(request, f"{ok} commission(s) recalculated.", messages.SUCCESS)
