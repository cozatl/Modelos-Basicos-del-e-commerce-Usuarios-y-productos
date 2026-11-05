# M4: BillingProfile (unused) is replaced by Payment, one row per payment attempt.

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0001_initial"),
        ("orders", "0003_order_checkout"),
    ]

    operations = [
        migrations.DeleteModel(name="BillingProfile"),
        migrations.CreateModel(
            name="Payment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(default="simulated", max_length=30)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("approved", "Approved"), ("declined", "Declined"), ("refunded", "Refunded")], default="pending", max_length=20)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(0)])),
                ("currency", models.CharField(default="MXN", max_length=3)),
                ("reference", models.CharField(max_length=100, unique=True)),
                ("failure_reason", models.CharField(blank=True, max_length=255)),
                ("raw_response", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("refunded_at", models.DateTimeField(blank=True, null=True)),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payments", to="orders.order")),
            ],
            options={
                "ordering": ("-created_at", "-id"),
                "indexes": [
                    models.Index(fields=["order", "status"], name="billing_pay_order_i_5e2c1a_idx"),
                ],
            },
        ),
    ]
