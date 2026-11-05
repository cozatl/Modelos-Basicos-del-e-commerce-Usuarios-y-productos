# Starting point for shipping: three zones and the settings row, so checkout works
# right after migrating. The costs are PLACEHOLDERS: change them in
# /admin/addresses/shippingzone/ and add your postal codes to Zona 1 and Zona 2.
# Any postal code not listed falls into Zona 3 (the default zone).

from decimal import Decimal

from django.db import migrations

ZONES = [
    ("Zona 1", Decimal("0.00"), True, 1),
    ("Zona 2", Decimal("99.00"), False, 2),
    ("Zona 3", Decimal("199.00"), False, 3),
]


def seed(apps, schema_editor):
    ShippingZone = apps.get_model("addresses", "ShippingZone")
    ShippingSettings = apps.get_model("addresses", "ShippingSettings")
    zones = {}
    for name, cost, free, order in ZONES:
        zones[name], _ = ShippingZone.objects.get_or_create(
            name=name,
            defaults={"cost": cost, "allows_free_shipping": free, "sort_order": order},
        )
    ShippingSettings.objects.get_or_create(
        pk=1, defaults={"default_zone": zones["Zona 3"]}
    )


class Migration(migrations.Migration):

    dependencies = [("addresses", "0002_address_mx_shipping_zones")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
