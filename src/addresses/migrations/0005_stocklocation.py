# M5: stock locations (online warehouse + physical stores), minimal version.
# Seeds the online warehouse so online orders always have a location.

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


def seed(apps, schema_editor):
    StockLocation = apps.get_model("addresses", "StockLocation")
    if not StockLocation.objects.filter(kind="warehouse").exists():
        StockLocation.objects.create(
            kind="warehouse", name="Almacén en línea", region="En línea", sort_order=0
        )


class Migration(migrations.Migration):

    dependencies = [("addresses", "0004_alter_shippingzone_cost")]

    operations = [
        migrations.CreateModel(
            name="StockLocation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("warehouse", "Almacén en línea"), ("store", "Tienda física")], default="store", max_length=20)),
                ("name", models.CharField(max_length=80, unique=True)),
                ("region", models.CharField(help_text='Groups branches, e.g. "Puebla" or "Mérida".', max_length=80, verbose_name="región / ciudad")),
                ("state", models.CharField(blank=True, choices=MEXICAN_STATES, max_length=3)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ("sort_order", "region", "name")},
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
