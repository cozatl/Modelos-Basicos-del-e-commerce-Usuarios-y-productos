# M4: Address is rebuilt (per user, Mexican fields, default flag) and the
# shipping-zone tables are added. Old addresses are dropped (test data only).

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

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


class Migration(migrations.Migration):

    dependencies = [
        ("addresses", "0001_initial"),
        # BillingProfile had a FK to Address: it must be gone first
        ("billing", "0002_payment_remove_billingprofile"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.DeleteModel(name="Address"),
        migrations.CreateModel(
            name="Address",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("label", models.CharField(blank=True, help_text='Optional name, e.g. "Casa", "Oficina".', max_length=50)),
                ("recipient_name", models.CharField(max_length=120)),
                ("phone", models.CharField(max_length=10, validators=[django.core.validators.RegexValidator("^\\d{10}$", "The phone number must have exactly 10 digits.")])),
                ("street", models.CharField(max_length=120)),
                ("exterior_number", models.CharField(max_length=20)),
                ("interior_number", models.CharField(blank=True, max_length=20)),
                ("neighborhood", models.CharField(max_length=120, verbose_name="colonia")),
                ("postal_code", models.CharField(max_length=5, validators=[django.core.validators.RegexValidator("^\\d{5}$", "The postal code must have exactly 5 digits.")])),
                ("municipality", models.CharField(max_length=120, verbose_name="municipio / alcaldía")),
                ("city", models.CharField(blank=True, max_length=120)),
                ("state", models.CharField(choices=MEXICAN_STATES, max_length=3)),
                ("references", models.TextField(blank=True, help_text="Between streets, facade colour, etc.")),
                ("is_default", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="addresses", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name_plural": "addresses",
                "ordering": ("-is_default", "-updated_at", "-id"),
                "constraints": [
                    models.UniqueConstraint(condition=models.Q(("is_default", True)), fields=("user",), name="one_default_address_per_user"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ShippingZone",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=60, unique=True)),
                ("cost", models.DecimalField(decimal_places=2, help_text="Flat cost per order, VAT included. 0 = free shipping in this zone.", max_digits=10, validators=[django.core.validators.MinValueValidator(0)])),
                ("postal_codes", models.TextField(blank=True, help_text="5-digit postal codes, one per line or separated by commas.")),
                ("allows_free_shipping", models.BooleanField(default=False, help_text='Free shipping over the threshold applies here when the shipping settings limit it to "marked zones only".')),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
            ],
            options={"ordering": ("sort_order", "name")},
        ),
        migrations.CreateModel(
            name="ShippingSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("free_shipping_threshold", models.DecimalField(blank=True, decimal_places=2, help_text="Orders whose products add up to this amount (VAT included) ship for free. Empty = no free shipping by amount.", max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)])),
                ("free_shipping_only_marked_zones", models.BooleanField(default=False, help_text='If checked, free shipping by amount only applies in zones marked with "allows free shipping". If not, it applies everywhere.')),
                ("default_zone", models.ForeignKey(blank=True, help_text="Zone (and cost) used for postal codes that are not in any zone.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="addresses.shippingzone")),
            ],
            options={
                "verbose_name": "shipping settings",
                "verbose_name_plural": "shipping settings",
            },
        ),
    ]
