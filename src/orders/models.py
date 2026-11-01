import uuid
from datetime import timedelta
from decimal import Decimal
from typing import ClassVar

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Avg, Sum
from django.utils import timezone

from products.models import Product


class OrderQuerySet(models.query.QuerySet):
    def recent(self):
        return self.order_by("-updated", "-timestamp")

    def get_sales_breakdown(self):
        recent = self.recent().not_refunded()
        recent_data = recent.totals_data()
        recent_cart_data = recent.cart_data()
        shipped = recent.by_status(status="shipped")
        shipped_data = shipped.totals_data()
        paid = recent.by_status(status="paid")
        paid_data = paid.totals_data()
        data = {
            "recent": recent,
            "recent_data": recent_data,
            "recent_cart_data": recent_cart_data,
            "shipped": shipped,
            "shipped_data": shipped_data,
            "paid_data": paid_data,
        }
        return data

    def by_status(self, status="shipped"):
        return self.filter(status=status)

    def not_refunded(self):
        return self.exclude(status="refunded")

    def not_created(self):
        return self.exclude(status="created")

    def by_range(self, start_date, end_date=None):
        if end_date is None:
            return self.filter(created_at__gte=start_date)
        return self.filter(created_at__gte=start_date).filter(
            created_at__lte=end_date
        )

    def totals_data(self):
        return self.aggregate(Sum("total"), Avg("total"))

    def by_weeks_range(self, weeks_ago=7, number_of_weeks=2):
        number_of_weeks = min(number_of_weeks, weeks_ago)
        days_ago_start = weeks_ago * 7
        days_ago_end = days_ago_start - (number_of_weeks * 7)
        start_date = timezone.now() - timedelta(days=days_ago_start)
        end_date = timezone.now() - timedelta(days=days_ago_end)
        return self.by_range(start_date, end_date=end_date)


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
    """discount_price when set, otherwise price (None for products that cannot be bought)."""
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
        return "This product cannot be bought; it is only available by subscription or for free."
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
    quantity = models.PositiveIntegerField(
        default=1, validators=[MinValueValidator(1)]
    )
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


class Order(models.Model):
    cart = models.OneToOneField(Cart, on_delete=models.CASCADE)
    order_id = models.CharField(max_length=120, blank=True, unique=True)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(auto_now_add=True)
    # created_at = models.DateTimeField()

    def save(self, *args, **kwargs):
        if not self.order_id:
            self.order_id = str(uuid.uuid4()).replace("-", "").upper()[:12]
        super().save(*args, **kwargs)

    def __str__(self):
        return self.order_id

    objects = OrderQuerySet.as_manager()
