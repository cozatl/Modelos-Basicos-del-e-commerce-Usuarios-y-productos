"""
Checkout and order life cycle. Views and the admin call these functions; nothing
else changes an order's status or the stock it reserved.

Locking: every function that changes an order locks its row (select_for_update)
inside a transaction, and stock changes use F() expressions on locked product rows,
so the release task, a payment and a cancellation cannot step on each other.
"""

import logging
import smtplib
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerOperationalError

from addresses.shipping import ShippingNotAvailable, quote_shipping
from billing.models import PaymentStatus
from billing.payments import get_provider
from products.models import Product

from .choices import SOLD_STATUSES, OrderStatus
from .models import (
    Cart,
    CartItem,
    Order,
    OrderItem,
    effective_price,
    max_quantity_for,
    purchase_problem,
)

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


class CheckoutError(Exception):
    """
    A business rule stopped the operation. ``code`` lets the view choose the HTTP
    status (conflicts -> 409, the rest -> 400); ``field`` places the message.
    """

    def __init__(self, message, code="invalid", field=None, details=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.field = field
        self.details = details


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def tax_rate():
    return Decimal(str(getattr(settings, "TAX_RATE", "0.16")))


def tax_included(amount, rate):
    """VAT contained in a price that already includes it: 116.00 @16% -> 16.00."""
    return money(amount - amount / (1 + rate))


def reservation_minutes():
    return int(getattr(settings, "ORDER_RESERVATION_MINUTES", 10))


# ---------------------------------------------------------------------------------
# Quote (no side effects): what the checkout page shows before placing the order
# ---------------------------------------------------------------------------------
@dataclass
class Totals:
    items_subtotal: Decimal
    needs_shipping: bool
    shipping_cost: Decimal
    shipping_zone_name: str
    free_shipping_applied: bool
    total: Decimal
    tax_rate: Decimal
    tax_amount: Decimal


def compute_totals(lines, postal_code=None):
    """
    ``lines``: iterable of (product, quantity). Raises CheckoutError when the order
    needs shipping and the postal code is missing or not served.
    """
    items_subtotal = money(sum((effective_price(p) * q for p, q in lines), ZERO))
    needs_shipping = any(p.is_physical for p, _ in lines)
    shipping_cost, zone_name, free = ZERO, "", False
    if needs_shipping:
        if not postal_code:
            raise CheckoutError(
                "A shipping address is required for physical products.",
                field="address",
            )
        try:
            quote = quote_shipping(postal_code, items_subtotal)
        except ShippingNotAvailable as exc:
            raise CheckoutError(str(exc), field="address") from exc
        shipping_cost = money(quote.cost)
        zone_name = quote.zone_name
        free = quote.free_by_threshold
    total = items_subtotal + shipping_cost
    rate = tax_rate()
    return Totals(
        items_subtotal=items_subtotal,
        needs_shipping=needs_shipping,
        shipping_cost=shipping_cost,
        shipping_zone_name=zone_name,
        free_shipping_applied=free,
        total=total,
        tax_rate=rate,
        tax_amount=tax_included(total, rate),
    )


def cart_problems(items):
    """{product slug: message} for the rows that cannot be bought right now."""
    problems = {}
    for item in items:
        problem = purchase_problem(item.product, item.quantity)
        if problem:
            problems[item.product.slug] = problem
    return problems


def quote_cart(user, postal_code=None):
    cart = Cart.for_user(user)
    items = list(cart.items.select_related("product"))
    if not items:
        raise CheckoutError("Your cart is empty.", field="cart")
    problems = cart_problems(items)
    if problems:
        raise CheckoutError(
            "Some products in your cart cannot be bought.",
            field="cart",
            details=problems,
        )
    return compute_totals([(i.product, i.quantity) for i in items], postal_code)


# ---------------------------------------------------------------------------------
# Create the order (reserve stock)
# ---------------------------------------------------------------------------------
@transaction.atomic
def create_order_from_cart(user, address=None, notes=""):
    """
    Turns the user's cart into a pending order: freezes prices, copies the address,
    takes the stock of physical products and empties the cart. The order must be
    paid before ``expires_at`` or the stock goes back (see release_expired_orders).
    """
    cart = _lock_cart(user)
    items = list(cart.items.all())
    if not items:
        raise CheckoutError("Your cart is empty.", field="cart")

    # Lock the products in a fixed order (by id) so two checkouts never deadlock.
    products = {
        p.pk: p
        for p in Product.objects.select_for_update()
        .filter(pk__in=[i.product_id for i in items])
        .order_by("pk")
    }
    for item in items:
        item.product = products[item.product_id]

    problems = cart_problems(items)
    if problems:
        raise CheckoutError(
            "Some products in your cart cannot be bought.",
            field="cart",
            details=problems,
        )

    lines = [(i.product, i.quantity) for i in items]
    totals = compute_totals(lines, address.postal_code if address else None)

    now = timezone.now()
    order = Order.objects.create(
        user=user,
        customer_email=user.email,
        status=OrderStatus.PENDING_PAYMENT,
        currency=getattr(settings, "CURRENCY", "MXN"),
        items_subtotal=totals.items_subtotal,
        shipping_cost=totals.shipping_cost,
        total=totals.total,
        tax_rate=totals.tax_rate,
        tax_amount=totals.tax_amount,
        needs_shipping=totals.needs_shipping,
        shipping_zone_name=totals.shipping_zone_name,
        free_shipping_applied=totals.free_shipping_applied,
        notes=(notes or "").strip(),
        expires_at=now + timedelta(minutes=reservation_minutes()),
        # Optional for digital-only orders, but kept when given.
        **(address.as_snapshot() if address else {}),
    )
    OrderItem.objects.bulk_create(
        [
            OrderItem(
                order=order,
                product=product,
                product_name=product.name,
                sku=product.sku,
                product_type=product.product_type,
                unit_price=money(effective_price(product)),
                quantity=quantity,
                subtotal=money(effective_price(product) * quantity),
            )
            for product, quantity in lines
        ]
    )
    # Reserve: physical stock leaves the shelf now.
    for product, quantity in lines:
        if product.is_physical:
            Product.objects.filter(pk=product.pk).update(stock=F("stock") - quantity)

    cart.items.all().delete()
    cart.save(update_fields=("updated_at",))
    return order


# ---------------------------------------------------------------------------------
# Stock release helpers (call with the order row locked)
# ---------------------------------------------------------------------------------
def _release_stock(order):
    """Give the reserved physical units back to their products (only once)."""
    if order.stock_released:
        return
    for item in order.items.all():
        if item.is_physical and item.product_id:
            Product.objects.filter(pk=item.product_id).update(
                stock=F("stock") + item.quantity
            )
    order.stock_released = True


def _lock_cart(user):
    return Cart.objects.select_for_update().get(pk=Cart.for_user(user).pk)


def _restore_to_cart(order, cart):
    """Best effort: put the products of an expired order back in the user's cart."""
    for item in order.items.select_related("product"):
        product = item.product
        if product is None:
            continue
        existing = cart.items.filter(product=product).first()
        wanted = item.quantity + (existing.quantity if existing else 0)
        quantity = min(wanted, max_quantity_for(product))
        if quantity < 1 or purchase_problem(product, quantity):
            continue
        if existing:
            existing.quantity = quantity
            existing.save(update_fields=("quantity", "updated_at"))
        else:
            CartItem.objects.create(cart=cart, product=product, quantity=quantity)
    cart.save(update_fields=("updated_at",))


def _lock(order_pk, **filters):
    return Order.objects.select_for_update().get(pk=order_pk, **filters)


def _expire_locked(order):
    # Same lock order as create_order_from_cart (cart, then products) so a checkout
    # and an expiration of the same user can never deadlock.
    cart = _lock_cart(order.user)
    _release_stock(order)
    _restore_to_cart(order, cart)
    order.status = OrderStatus.EXPIRED
    order.cancelled_at = timezone.now()
    order.save()


# ---------------------------------------------------------------------------------
# Expiration
# ---------------------------------------------------------------------------------
@transaction.atomic
def expire_order(order_pk):
    """Expire one order if it is still pending and overdue. Returns True if it did."""
    order = _lock(order_pk)
    if not order.is_overdue:
        return False
    _expire_locked(order)
    logger.info("Order %s expired, stock released", order.order_id)
    return True


def release_expired_orders(limit=500):
    """Run by Celery beat every minute (and on demand by views).
    Returns how many expired."""
    pks = list(Order.objects.due_to_expire().values_list("pk", flat=True)[:limit])
    return sum(1 for pk in pks if expire_order(pk))


def expire_user_overdue_orders(user):
    """On-the-fly safety net in case the periodic task is not running."""
    for pk in (
        Order.objects.filter(user=user).due_to_expire().values_list("pk", flat=True)
    ):
        expire_order(pk)


# ---------------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------------
EXPIRED_MESSAGE = "The reservation expired. The products went back to your cart."


def pay_order(order, outcome="approved"):
    """
    Charge the order with the configured provider. Declined payments leave the order
    pending (the customer can retry until it expires). Returns (order, payment).
    """
    # The error is raised outside the transaction so an expiration found here is
    # committed (raising inside transaction.atomic would roll it back).
    order, payment, error = _pay_order(order.pk, outcome)
    if error:
        raise error
    return order, payment


@transaction.atomic
def _pay_order(order_pk, outcome):
    order = _lock(order_pk)
    if order.is_overdue:
        _expire_locked(order)
        return order, None, CheckoutError(EXPIRED_MESSAGE, code="expired")
    if order.status != OrderStatus.PENDING_PAYMENT:
        return (
            order,
            None,
            CheckoutError("This order cannot be paid.", code="not_payable"),
        )

    payment = get_provider().charge(order, outcome=outcome)
    if payment.status == PaymentStatus.APPROVED:
        order.status = OrderStatus.PAID
        order.paid_at = timezone.now()
        order.save(update_fields=("status", "paid_at", "updated_at"))
        transaction.on_commit(lambda: notify_order_paid(order_pk))
    return order, payment, None


def notify_order_paid(order_pk):
    """Queue the confirmation email; send it inline if the broker is unreachable."""
    from .tasks import send_order_confirmation_task

    try:
        send_order_confirmation_task.delay(order_pk)
    # broker down: do not lose the email nor fail the payment
    except BrokerOperationalError:
        logger.warning(
            "Celery unavailable, sending confirmation for order %s inline", order_pk
        )
        try:
            from .emails import send_order_confirmation

            send_order_confirmation(Order.objects.get(pk=order_pk))
        except (smtplib.SMTPException, OSError, Order.DoesNotExist):
            logger.exception(
                "Could not send the confirmation email of order %s", order_pk
            )


# ---------------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------------
def customer_cancel(order, reason=""):
    """
    Unpaid orders are cancelled right away (stock goes back). Paid ones only get a
    cancellation request that staff approves or rejects from the admin.
    """
    order, error = _customer_cancel(order.pk, (reason or "").strip())
    if error:
        raise error
    return order


@transaction.atomic
def _customer_cancel(order_pk, reason):
    order = _lock(order_pk)
    if order.is_overdue:
        _expire_locked(order)
        return order, CheckoutError("This order already expired.", code="expired")

    if order.status == OrderStatus.PENDING_PAYMENT:
        _release_stock(order)
        order.status = OrderStatus.CANCELLED
        order.cancelled_at = timezone.now()
        order.cancel_reason = reason
        order.save()
        return order, None

    if order.status in SOLD_STATUSES:
        if order.cancel_requested_at:
            return order, CheckoutError(
                "A cancellation was already requested.", code="conflict"
            )
        order.cancel_requested_at = timezone.now()
        order.cancel_reason = reason
        order.save(
            update_fields=(
                "cancel_requested_at",
                "cancel_reason",
                "updated_at",
            )
        )
        return order, None

    return order, CheckoutError("This order cannot be cancelled.", code="conflict")


# ---------------------------------------------------------------------------------
# Staff actions (admin)
# ---------------------------------------------------------------------------------
def _append_note(order, text):
    stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M")
    order.staff_notes = f"{order.staff_notes}\n[{stamp}] {text}".strip()


@transaction.atomic
def mark_shipped(order, staff_user=None):
    order = _lock(order.pk)
    if order.status != OrderStatus.PAID:
        raise CheckoutError(f"Order {order.order_id} is not paid.", code="conflict")
    if not order.needs_shipping:
        raise CheckoutError(
            f"Order {order.order_id} only has digital products.",
            code="conflict",
        )
    order.status = OrderStatus.SHIPPED
    order.shipped_at = timezone.now()
    _append_note(order, f"Marked as shipped by {staff_user or 'system'}.")
    order.save()
    return order


@transaction.atomic
def mark_delivered(order, staff_user=None):
    order = _lock(order.pk)
    # Digital-only orders go straight from paid to delivered.
    allowed = (
        (OrderStatus.SHIPPED,)
        if order.needs_shipping
        else (OrderStatus.PAID, OrderStatus.SHIPPED)
    )
    if order.status not in allowed:
        raise CheckoutError(
            f"Order {order.order_id} cannot be marked as delivered.",
            code="conflict",
        )
    order.status = OrderStatus.DELIVERED
    order.delivered_at = timezone.now()
    _append_note(order, f"Marked as delivered by {staff_user or 'system'}.")
    order.save()
    return order


@transaction.atomic
def approve_cancellation(order, restock=True, staff_user=None):
    """
    Staff decision. Pending orders are cancelled (stock always returns). Paid orders
    are refunded through the provider; their stock returns only if ``restock`` (the
    goods came back in sellable condition, or were never shipped).
    """
    order = _lock(order.pk)
    now = timezone.now()
    if order.status == OrderStatus.PENDING_PAYMENT:
        _release_stock(order)
        order.status = OrderStatus.CANCELLED
    elif order.status in SOLD_STATUSES:
        provider = get_provider()
        for payment in order.payments.filter(status=PaymentStatus.APPROVED):
            provider.refund(payment)
        if restock:
            _release_stock(order)
        order.status = OrderStatus.REFUNDED
    else:
        raise CheckoutError(
            f"Order {order.order_id} cannot be cancelled.", code="conflict"
        )
    order.cancelled_at = now
    _append_note(
        order,
        f"Cancellation approved by {staff_user or 'system'}"
        f" ({'stock returned' if order.stock_released else 'stock not returned'}).",
    )
    order.save()
    return order


@transaction.atomic
def reject_cancellation(order, staff_user=None):
    order = _lock(order.pk)
    if not order.cancel_requested_at:
        raise CheckoutError(
            f"Order {order.order_id} has no cancellation request.",
            code="conflict",
        )
    _append_note(
        order,
        f"Cancellation request of {order.cancel_requested_at:%Y-%m-%d %H:%M} rejected "
        f"by {staff_user or 'system'}. Reason given: {order.cancel_reason or '-'}",
    )
    order.cancel_requested_at = None
    order.save()
    return order
