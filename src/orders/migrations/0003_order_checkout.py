# M4: Order is rebuilt (user + OrderItem instead of a OneToOne to Cart).
# Existing orders are dropped on purpose (test data only).

import django.core.validators
import django.db.models.deletion
from decimal import Decimal

from django.conf import settings
from django.db import migrations, models

import orders.models

MEXICAN_STATES = [
    ("AGU", "Aguascalientes"), ("BCN", "Baja California"), ("BCS", "Baja California Sur"),
    ("CAM", "Campeche"), ("CHP", "Chiapas"), ("CHH", "Chihuahua"),
    ("CMX", "Ciudad de México"), ("COA", "Coahuila"), ("COL", "Colima"),
    ("DUR", "Durango"), ("GUA", "Guanajuato"), ("GRO", "Guerrero"), ("HID", "Hidalgo"),
    ("JAL", "Jalisco"), ("MEX", "Estado de México"), ("MIC", "Michoacán"),
    ("MOR", "Morelos"), ("NAY", "Nayarit"), ("NLE", "Nuevo León"), ("OAX", "Oaxaca"),
    ("PUE", "Puebla"), ("QUE", "Querétaro"), ("ROO", "Quintana Roo"),
    ("SLP", "San Luis Potosí"), ("SIN", "Sinaloa"), ("SON", "Sonora"),
    ("TAB", "Tabasco"), ("TAM", "Tamaulipas"), ("TLA", "Tlaxcala"), ("VER", "Veracruz"),
    ("YUC", "Yucatán"), ("ZAC", "Zacatecas"),
]

STATUSES = [
    ("pending_payment", "Pending payment"),
    ("paid", "Paid"),
    ("shipped", "Shipped"),
    ("delivered", "Delivered"),
    ("expired", "Expired"),
    ("cancelled", "Cancelled"),
    ("refunded", "Refunded"),
]


def money(**extra):
    return models.DecimalField(
        decimal_places=2,
        max_digits=12,
        validators=[django.core.validators.MinValueValidator(0)],
        **extra,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0002_cart_per_user_cartitem"),
        ("products", "0003_alter_product_options_category_product_category"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.DeleteModel(name="Order"),
        migrations.CreateModel(
            name="Order",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("order_id", models.CharField(default=orders.models.generate_order_id, editable=False, max_length=12, unique=True)),
                ("customer_email", models.EmailField(help_text="Email at the time of purchase.", max_length=254)),
                ("status", models.CharField(choices=STATUSES, db_index=True, default="pending_payment", max_length=20)),
                ("currency", models.CharField(default="MXN", max_length=3)),
                ("items_subtotal", money()),
                ("shipping_cost", money(default=Decimal("0.00"))),
                ("total", money()),
                ("tax_rate", models.DecimalField(decimal_places=4, help_text="0.1600 = 16 %", max_digits=5)),
                ("tax_amount", money()),
                ("needs_shipping", models.BooleanField(help_text="False when the order only has digital products.")),
                ("shipping_zone_name", models.CharField(blank=True, max_length=60)),
                ("free_shipping_applied", models.BooleanField(default=False, help_text="Shipping waived by the free-shipping threshold.")),
                ("recipient_name", models.CharField(blank=True, max_length=120)),
                ("phone", models.CharField(blank=True, max_length=10)),
                ("street", models.CharField(blank=True, max_length=120)),
                ("exterior_number", models.CharField(blank=True, max_length=20)),
                ("interior_number", models.CharField(blank=True, max_length=20)),
                ("neighborhood", models.CharField(blank=True, max_length=120, verbose_name="colonia")),
                ("postal_code", models.CharField(blank=True, max_length=5)),
                ("municipality", models.CharField(blank=True, max_length=120, verbose_name="municipio / alcaldía")),
                ("city", models.CharField(blank=True, max_length=120)),
                ("state", models.CharField(blank=True, choices=MEXICAN_STATES, max_length=3)),
                ("references", models.TextField(blank=True)),
                ("tracking_number", models.CharField(blank=True, max_length=100)),
                ("notes", models.TextField(blank=True, help_text="Written by the customer.")),
                ("staff_notes", models.TextField(blank=True, help_text="Internal, never shown to the customer.")),
                ("expires_at", models.DateTimeField(blank=True, help_text="Unpaid orders release their stock at this time.", null=True)),
                ("stock_released", models.BooleanField(default=False, help_text="The reserved stock was given back to the products.")),
                ("cancel_requested_at", models.DateTimeField(blank=True, null=True)),
                ("cancel_reason", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("shipped_at", models.DateTimeField(blank=True, null=True)),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, help_text="When it was cancelled, expired or refunded.", null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="orders", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ("-created_at", "-id"),
                "permissions": [
                    ("change_order_status", "Can mark orders as shipped or delivered"),
                    ("approve_order_cancellation", "Can approve or reject cancellations and refunds"),
                ],
                "indexes": [
                    models.Index(fields=["status", "expires_at"], name="orders_orde_status_7c1a2e_idx"),
                    models.Index(fields=["user", "-created_at"], name="orders_orde_user_id_4b9f0d_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="OrderItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("product_name", models.CharField(max_length=100)),
                ("sku", models.CharField(max_length=50)),
                ("product_type", models.CharField(max_length=20)),
                ("unit_price", money()),
                ("quantity", models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                ("subtotal", money()),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="orders.order")),
                ("product", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="order_items", to="products.product")),
            ],
            options={
                "ordering": ("id",),
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("quantity__gte", 1)), name="order_item_quantity_gte_1"),
                ],
            },
        ),
    ]
