"""
Shipping cost by postal-code zones.

    quote = quote_shipping("01000", subtotal=Decimal("850.00"))
    quote.cost            # Decimal("0.00") / Decimal("99.00") ...
    quote.zone_name       # "Zona 1"

Rules (all configurable in the admin, see ShippingZone and ShippingSettings):
1. The postal code is looked up in the active zones. If it is not in any zone, the
   "default zone" of ShippingSettings is used.
2. The zone cost is the flat cost of the order (0 = free in that zone).
3. If a free-shipping threshold is set and the products subtotal reaches it, shipping
   is free — everywhere, or only in zones marked "allows free shipping" when
   ShippingSettings.free_shipping_only_marked_zones is on.
"""

from dataclasses import dataclass
from decimal import Decimal

from .models import ShippingSettings, ShippingZone

ZERO = Decimal("0.00")


class ShippingNotAvailable(Exception):
    """No zone matches the postal code and no default zone is configured."""


@dataclass(frozen=True)
class ShippingQuote:
    zone: ShippingZone
    base_cost: Decimal
    cost: Decimal
    free_by_threshold: bool
    used_default_zone: bool

    @property
    def zone_name(self):
        return self.zone.name


def find_zone(postal_code):
    """The active zone listing this postal code, or None."""
    postal_code = (postal_code or "").strip()
    for zone in ShippingZone.objects.filter(is_active=True):
        if postal_code in zone.postal_code_list:
            return zone
    return None


def quote_shipping(postal_code, subtotal):
    shipping_settings = ShippingSettings.load()
    zone = find_zone(postal_code)
    used_default = zone is None
    if zone is None:
        zone = shipping_settings.default_zone
        if zone is None or not zone.is_active:
            raise ShippingNotAvailable("We do not ship to this postal code yet.")

    base_cost = zone.cost
    threshold = shipping_settings.free_shipping_threshold
    free_by_threshold = (
        base_cost > ZERO
        and threshold is not None
        and subtotal >= threshold
        and (
            not shipping_settings.free_shipping_only_marked_zones
            or zone.allows_free_shipping
        )
    )
    return ShippingQuote(
        zone=zone,
        base_cost=base_cost,
        cost=ZERO if free_by_threshold else base_cost,
        free_by_threshold=free_by_threshold,
        used_default_zone=used_default,
    )
