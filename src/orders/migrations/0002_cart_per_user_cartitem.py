# M3: one cart per user with quantities (CartItem) instead of a plain ManyToMany.

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def delete_carts_without_user(apps, schema_editor):
    # Carts are now always tied to a user; anonymous leftovers cannot be migrated.
    Cart = apps.get_model("orders", "Cart")
    Cart.objects.filter(user__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0001_initial"),
        ("products", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(delete_carts_without_user, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="cart",
            name="products",
        ),
        migrations.AlterField(
            model_name="cart",
            name="user",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="cart",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="cart",
            name="created_at",
            field=models.DateTimeField(
                auto_now_add=True, default=django.utils.timezone.now
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="cart",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.CreateModel(
            name="CartItem",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "quantity",
                    models.PositiveIntegerField(
                        default=1,
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
                ("added_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "cart",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="orders.cart",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cart_items",
                        to="products.product",
                    ),
                ),
            ],
            options={
                "ordering": ("added_at", "id"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("cart", "product"), name="unique_product_per_cart"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(quantity__gte=1),
                        name="cart_item_quantity_gte_1",
                    ),
                ],
            },
        ),
        migrations.AddField(
            model_name="cart",
            name="products",
            field=models.ManyToManyField(
                blank=True,
                related_name="carts",
                through="orders.CartItem",
                to="products.product",
            ),
        ),
    ]
