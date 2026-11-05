from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

# Group name -> permission codenames (app "orders" unless "app.codename").
GROUPS = {
    "Pedidos: consulta": [
        "view_order",
        "view_orderitem",
        "billing.view_payment",
    ],
    "Pedidos: envíos": [
        "view_order",
        "change_order",
        "view_orderitem",
        "change_order_status",
        "billing.view_payment",
    ],
    "Pedidos: cancelaciones y reembolsos": [
        "view_order",
        "change_order",
        "view_orderitem",
        "approve_order_cancellation",
        "billing.view_payment",
    ],
    "Envíos: configuración": [
        "addresses.view_shippingzone",
        "addresses.add_shippingzone",
        "addresses.change_shippingzone",
        "addresses.delete_shippingzone",
        "addresses.view_shippingsettings",
        "addresses.change_shippingsettings",
    ],
    # M5: reports. Permissions add up (branch + own sales).
    "Reportes: global": [
        "analytics.view_global_reports",
    ],
    "Reportes: sucursal": [
        "analytics.view_location_reports",
    ],
    "Reportes: mis ventas": [
        "analytics.view_own_sales",
    ],
    "Comisiones: configuración": [
        "analytics.view_staffprofile",
        "analytics.add_staffprofile",
        "analytics.change_staffprofile",
        "view_order",
        "change_order",
        "assign_order_seller",
        "addresses.view_stocklocation",
    ],
    "Sucursales: configuración": [
        "addresses.view_stocklocation",
        "addresses.add_stocklocation",
        "addresses.change_stocklocation",
    ],
}


class Command(BaseCommand):
    help = (
        "Create (or update) the staff groups for orders, shipping, reports and "
        "commissions. Safe to re-run."
    )

    def handle(self, *args, **options):
        for name, codenames in GROUPS.items():
            group, created = Group.objects.get_or_create(name=name)
            perms = []
            for full in codenames:
                app_label, codename = full.split(".") if "." in full else ("orders", full)
                perms.append(
                    Permission.objects.get(
                        content_type__app_label=app_label, codename=codename
                    )
                )
            group.permissions.add(*perms)
            verb = "Created" if created else "Updated"
            self.stdout.write(f"{verb} group '{name}' ({len(perms)} permissions)")
        self.stdout.write(
            self.style.SUCCESS(
                "Done. Add staff users to these groups in /admin/accounts/user/."
            )
        )
