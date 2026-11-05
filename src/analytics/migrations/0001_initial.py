# M5: staff profile (assigned branches + commission percentage) and report permissions.

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("addresses", "0005_stocklocation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StaffProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("commission_percent", models.DecimalField(blank=True, decimal_places=2, help_text="Percentage of each sale credited to this seller, e.g. 10 = 10 %. Empty = no commission. Applies to sales assigned from now on.", max_digits=5, null=True, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)])),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("locations", models.ManyToManyField(blank=True, help_text='Branches whose reports this person sees (group "Reportes: sucursal").', related_name="staff_profiles", to="addresses.stocklocation")),
                ("user", models.OneToOneField(limit_choices_to={"is_staff": True}, on_delete=django.db.models.deletion.CASCADE, related_name="staff_profile", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "staff profile",
                "permissions": [
                    ("view_global_reports", "Can see the reports of every branch and channel"),
                    ("view_location_reports", "Can see the reports of the assigned branches"),
                    ("view_own_sales", "Can see the reports of their own sales"),
                ],
            },
        ),
    ]
