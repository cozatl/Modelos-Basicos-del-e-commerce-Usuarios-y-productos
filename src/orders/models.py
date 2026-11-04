import math
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import ClassVar

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Avg, Count, Sum
from django.utils import timezone

from addresses.choices import MexicanState
from products.models import Product

from .choices import SOLD_STATUSES, OrderStatus

# Product types delivered digitally: the customer buys one licence/copy,
# never more.
DIGITAL_PRODUCT_TYPES = ("software", "ebook", "video")


class Cart(models.Model):
    """One cart per user, created on the fly when first needed.
    See :meth:`for_user`.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cart",
    )
    # Kept so existing code that reads cart.products keeps working;
    # quantities live in CartItem.
    products = models.ManyToManyField(
        Product, through="CartItem", related_name="carts", blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Cart #{self.id} ({self.user})"

    @classmethod
    def for_user(cls, user):
        cart, _ = cls.objects.get_or_create(user=user)
        return cart

    # The totals are always computed from the products, never stored: prices can change
    # while the product sits in the cart. The frozen price belongs to OrderItem (M4).
    @property
    def total_quantity(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def total(self):
        return sum((item.subtotal for item in self.items.all()), Decimal("0.00"))

    @property
    def has_problems(self):
        return any(item.problem for item in self.items.all())


def effective_price(product):
    """discount_price when set, otherwise price
    (None for products that cannot be bought)."""
    if product.discount_price is not None:
        return product.discount_price
    return product.price


def is_digital(product):
    return product.product_type in DIGITAL_PRODUCT_TYPES


def max_quantity_for(product):
    """Largest quantity a cart may hold for this product."""
    if is_digital(product):
        return 1
    return max(product.stock or 0, 0)


def purchase_problem(product, quantity):
    """
    Returns a message explaining why `quantity` units of `product` cannot be in a cart,
    or None when it is fine. Used to validate writes and to flag stale items on reads.
    """
    if not product.is_active:
        return "This product is no longer available."
    if product.access_type != "purchase":
        return (
            "This product cannot be bought; it is only available by subscription "
            "or for free."
        )
    if effective_price(product) is None:
        return "This product has no price yet."
    if is_digital(product):
        if quantity > 1:
            return "Digital products can only be added once."
        return None
    stock = max(product.stock or 0, 0)
    if stock <= 0:
        return "This product is out of stock."
    if quantity > stock:
        return f"Only {stock} units of this product are in stock."
    return None


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="cart_items"
    )
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    added_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("added_at", "id")
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=("cart", "product"), name="unique_product_per_cart"
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="cart_item_quantity_gte_1",
            ),
        ]

    def __str__(self):
        return f"{self.quantity} x {self.product}"

    @property
    def unit_price(self):
        return effective_price(self.product)

    @property
    def subtotal(self):
        price = self.unit_price
        if price is None:
            return Decimal("0.00")
        return price * self.quantity

    @property
    def max_quantity(self):
        return max_quantity_for(self.product)

    @property
    def problem(self):
        return purchase_problem(self.product, self.quantity)


def generate_order_id():
    return uuid.uuid4().hex.upper()[:12]


class OrderQuerySet(models.QuerySet):
    def recent(self):
        return self.order_by("-updated_at", "-created_at")

    def by_status(self, status=OrderStatus.SHIPPED):
        return self.filter(status=status)

    def sold(self):
        """Paid, shipped or delivered: the orders that count as sales."""
        return self.filter(status__in=SOLD_STATUSES)

    def not_refunded(self):
        return self.exclude(status=OrderStatus.REFUNDED)

    def pending_payment(self):
        return self.filter(status=OrderStatus.PENDING_PAYMENT)

    def due_to_expire(self, now=None):
        return self.pending_payment().filter(expires_at__lte=now or timezone.now())

    def by_range(self, start_date, end_date=None):
        if end_date is None:
            return self.filter(created_at__gte=start_date)
        return self.filter(created_at__gte=start_date, created_at__lte=end_date)

    def totals_data(self):
        return self.aggregate(Sum("total"), Avg("total"), Count("id"))

    def by_weeks_range(self, weeks_ago=7, number_of_weeks=2):
        number_of_weeks = min(number_of_weeks, weeks_ago)
        days_ago_start = weeks_ago * 7
        days_ago_end = days_ago_start - (number_of_weeks * 7)
        start_date = timezone.now() - timedelta(days=days_ago_start)
        end_date = timezone.now() - timedelta(days=days_ago_end)
        return self.by_range(start_date, end_date=end_date)

    def get_sales_breakdown(self):
        recent = self.recent().sold()
        return {
            "recent": recent,
            "recent_data": recent.totals_data(),
            "shipped": recent.by_status(OrderStatus.SHIPPED),
            "shipped_data": recent.by_status(OrderStatus.SHIPPED).totals_data(),
            "paid_data": recent.by_status(OrderStatus.PAID).totals_data(),
        }


money_field = {
    "max_digits": 12,
    "decimal_places": 2,
    "validators": [MinValueValidator(0)],
}


class Order(models.Model):
    """
    Created from the cart at checkout. Everything needed to show, ship or audit the
    order is copied here (prices, product names, address, tax rate), so later
    changes to products, addresses or settings never rewrite history.

    Stock of physical products is taken when the order is created (the reservation)
    and given back if the order expires or is cancelled/refunded with restock
    (see orders.services).
    """

    order_id = models.CharField(
        max_length=12, unique=True, editable=False, default=generate_order_id
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    customer_email = models.EmailField(help_text="Email at the time of purchase.")
    status = models.CharField(
        max_length=20,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING_PAYMENT,
        db_index=True,
    )

    # Money. All prices already include VAT; tax_amount is the VAT contained in total.
    currency = models.CharField(max_length=3, default="MXN")
    items_subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    shipping_cost = models.DecimalField(
        default=Decimal("0.00"),
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    total = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(0)]
    )
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=4, help_text="0.1600 = 16 %"
    )
    tax_amount = models.DecimalField(**money_field)

    # Shipping
    needs_shipping = models.BooleanField(
        help_text="False when the order only has digital products."
    )
    shipping_zone_name = models.CharField(max_length=60, blank=True)
    free_shipping_applied = models.BooleanField(
        default=False,
        help_text="Shipping waived by the free-shipping threshold.",
    )
    # Copy of the address (same names as addresses.Address.SNAPSHOT_FIELDS).
    # Empty for digital-only orders without an address.
    recipient_name = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=10, blank=True)
    street = models.CharField(max_length=120, blank=True)
    exterior_number = models.CharField(max_length=20, blank=True)
    interior_number = models.CharField(max_length=20, blank=True)
    neighborhood = models.CharField("colonia", max_length=120, blank=True)
    postal_code = models.CharField(max_length=5, blank=True)
    municipality = models.CharField("municipio / alcaldía", max_length=120, blank=True)
    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=3, choices=MexicanState.choices, blank=True)
    references = models.TextField(blank=True)
    tracking_number = models.CharField(max_length=100, blank=True)

    notes = models.TextField(blank=True, help_text="Written by the customer.")
    staff_notes = models.TextField(
        blank=True, help_text="Internal, never shown to the customer."
    )

    # Reservation / cancellation bookkeeping
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Unpaid orders release their stock at this time.",
    )
    stock_released = models.BooleanField(
        default=False,
        help_text="The reserved stock was given back to the products.",
    )
    cancel_requested_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When it was cancelled, expired or refunded.",
    )

    objects = OrderQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at", "-id")
        indexes: ClassVar[list] = [
            models.Index(
                fields=("status", "expires_at"),
                name="orders_orde_status_7c1a2e_idx",
            ),
            models.Index(
                fields=("user", "-created_at"),
                name="orders_orde_user_id_4b9f0d_idx",
            ),
        ]
        permissions: ClassVar[list] = [
            ("change_order_status", "Can mark orders as shipped or delivered"),
            (
                "approve_order_cancellation",
                "Can approve or reject cancellations and refunds",
            ),
        ]

    def __str__(self):
        return self.order_id

    # --- helpers used by serializers, admin and services --------------------------
    @property
    def is_pending(self):
        return self.status == OrderStatus.PENDING_PAYMENT

    @property
    def is_overdue(self):
        """Pending and past its reservation time
        (not yet processed by the release task)."""
        return bool(
            self.is_pending and self.expires_at and self.expires_at <= timezone.now()
        )

    @property
    def expires_in_seconds(self):
        if not self.is_pending or not self.expires_at:
            return None
        # Rounded up: the browser's countdown must never reach 0 before the server
        # considers the order expired.
        return max(math.ceil((self.expires_at - timezone.now()).total_seconds()), 0)

    @property
    def can_pay(self):
        return self.is_pending and not self.is_overdue

    @property
    def can_cancel(self):
        """The customer can cancel it right away (nothing was paid)."""
        return self.can_pay

    @property
    def can_request_cancellation(self):
        """Paid orders need staff approval (refund / return)."""
        return self.status in SOLD_STATUSES and self.cancel_requested_at is None

    @property
    def subtotal_before_tax(self):
        return self.total - self.tax_amount

    @property
    def has_shipping_address(self):
        return bool(self.postal_code)

    def shipping_address_lines(self):
        if not self.has_shipping_address:
            return []
        interior = f" int. {self.interior_number}" if self.interior_number else ""
        place = ", ".join(
            part
            for part in (
                self.municipality,
                self.city,
                self.get_state_display(),
            )
            if part
        )
        lines = [
            self.recipient_name,
            f"{self.street} {self.exterior_number}{interior}",
            f"Col. {self.neighborhood}, CP {self.postal_code}",
            place,
            f"Tel. {self.phone}",
        ]
        if self.references:
            lines.append(f"Referencias: {self.references}")
        return lines


class OrderItem(models.Model):
    """A frozen copy of one cart row: price, name and type at the time of purchase."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,  # the order keeps its copy if the product is deleted
        null=True,
        blank=True,
        related_name="order_items",
    )
    product_name = models.CharField(max_length=100)
    sku = models.CharField(max_length=50)
    product_type = models.CharField(max_length=20)
    unit_price = models.DecimalField(**money_field)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    subtotal = models.DecimalField(**money_field)

    class Meta:
        ordering = ("id",)
        constraints: ClassVar[list] = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="order_item_quantity_gte_1",
            ),
        ]

    def __str__(self):
        return f"{self.quantity} x {self.product_name}"

    @property
    def is_physical(self):
        return self.product_type == "physical"
