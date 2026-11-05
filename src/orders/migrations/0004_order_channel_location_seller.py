# M5: sale context on Order (channel, location, seller, frozen commission).
# Existing orders are online orders: they get channel=online and the online warehouse.

import django.core.validators
import django.db.models.deletion
from decimal import Decimal

from django.conf import settings
from django.db import migrations, models


def fill_online_location(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    StockLocation = apps.get_model("addresses", "StockLocation")
    warehouse = (
        StockLocation.objects.filter(kind="warehouse", is_active=True)
        .order_by("sort_order", "pk")
        .first()
    )
    if warehouse:
        Order.objects.filter(location__isnull=True, channel="online").update(
            location=warehouse
        )


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0003_order_checkout"),
        ("addresses", "0005_stocklocation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="channel",
            field=models.CharField(choices=[("online", "En línea"), ("pos", "Mostrador")], db_index=True, default="online", max_length=10),
        ),
        migrations.AddField(
            model_name="order",
            name="location",
            field=models.ForeignKey(blank=True, help_text="Online warehouse for online orders; the branch for counter sales.", null=True, on_delete=django.db.models.deletion.PROTECT, related_name="orders", to="addresses.stocklocation"),
        ),
        migrations.AddField(
            model_name="order",
            name="sold_by",
            field=models.ForeignKey(blank=True, help_text="Staff member credited with the sale (commissions).", limit_choices_to={"is_staff": True}, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="sales", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="order",
            name="commission_percent",
            field=models.DecimalField(blank=True, decimal_places=2, help_text="10.00 = 10 %. Empty = no seller.", max_digits=5, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="commission_amount",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AlterModelOptions(
            name="order",
            options={
                "ordering": ("-created_at", "-id"),
                "permissions": [
                    ("change_order_status", "Can mark orders as shipped or delivered"),
                    ("approve_order_cancellation", "Can approve or reject cancellations and refunds"),
                    ("assign_order_seller", "Can assign the seller (commission) of an order"),
                ],
            },
        ),
        migrations.AddIndex(
            model_name="order",
            index=models.Index(fields=["status", "paid_at"], name="orders_status_paid_idx"),
        ),
        migrations.AddIndex(
            model_name="order",
            index=models.Index(fields=["location", "paid_at"], name="orders_location_paid_idx"),
        ),
        migrations.AddIndex(
            model_name="order",
            index=models.Index(fields=["sold_by", "paid_at"], name="orders_seller_paid_idx"),
        ),
        migrations.RunPython(fill_online_location, migrations.RunPython.noop),
    ]
