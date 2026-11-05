"""
Seller commissions.

The percentage comes from the seller's StaffProfile and is frozen on the order
(commission_percent + commission_amount) when the seller is assigned. Changing a
percentage later only affects sales assigned afterwards; use
``assign_seller(order, seller, recalculate=True)`` (admin action) to re-apply it.

Refunded orders keep their numbers but never count: reports only add up sold orders.

Base (settings.COMMISSION_BASE):
- "net"   (default): products without VAT and without shipping.
- "total": the full amount the customer paid (VAT and shipping included).
"""

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

BASE_LABELS = {
    "net": "Productos sin IVA y sin envío",
    "total": "Total cobrado (IVA y envío incluidos)",
}


def commission_base_mode():
    mode = getattr(settings, "COMMISSION_BASE", "net")
    return mode if mode in BASE_LABELS else "net"


def _money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def commission_base(order):
    if commission_base_mode() == "total":
        return _money(order.total)
    # items_subtotal includes VAT at the order's frozen rate
    return _money(order.items_subtotal / (1 + order.tax_rate))


def commission_for(order, percent):
    if percent is None:
        return ZERO
    return _money(commission_base(order) * Decimal(percent) / 100)


def current_percent(user):
    profile = getattr(user, "staff_profile", None) if user else None
    return profile.commission_percent if profile else None


@transaction.atomic
def assign_seller(order, seller, percent=None):
    """
    Credit ``order`` to ``seller`` (a staff user, or None to clear it) and freeze
    the commission. ``percent`` overrides the seller's current percentage.
    """
    from orders.models import Order

    order = Order.objects.select_for_update().get(pk=order.pk)
    if seller is not None and not seller.is_staff:
        raise ValueError("Only staff members can be credited with a sale.")
    if seller is None:
        order.sold_by = None
        order.commission_percent = None
        order.commission_amount = ZERO
    else:
        if percent is None:
            percent = current_percent(seller)
        order.sold_by = seller
        order.commission_percent = percent
        order.commission_amount = commission_for(order, percent)
    order.save(
        update_fields=(
            "sold_by",
            "commission_percent",
            "commission_amount",
            "updated_at",
        )
    )
    return order
