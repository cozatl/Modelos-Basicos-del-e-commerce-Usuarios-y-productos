"""M5: report API — access by group, scope, filters, numbers and commissions."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from addresses.choices import LocationKind
from addresses.models import StockLocation
from orders import services
from orders.choices import OrderChannel, OrderStatus
from orders.models import Cart, CartItem, Order, OrderItem
from orders.tests import make_product, make_user
from products.models import Category

from .commissions import assign_seller, commission_for
from .models import StaffProfile

MX = ZoneInfo("America/Mexico_City")
OCTOBER = {"start": "2026-10-01", "end": "2026-10-31"}


def at(day, hour=12, month=10, minute=0):
    """An aware datetime in Mexico City time (2026)."""
    return datetime(2026, month, day, hour, minute, tzinfo=MX)


def make_staff(email, *perms):
    user = make_user(email)
    user.is_staff = True
    user.save()
    for perm in perms:
        app_label, codename = perm.split(".")
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
        )
    return user


def make_store(name, region="Puebla"):
    return StockLocation.objects.create(kind=LocationKind.STORE, name=name, region=region)


def make_order(
    user,
    total="116.00",
    status_=OrderStatus.PAID,
    paid_at=None,
    channel=OrderChannel.ONLINE,
    location=None,
    sold_by=None,
    items=(),
    created_at=None,
    cancelled_at=None,
    needs_shipping=False,
    shipping="0.00",
    **extra,
):
    total = Decimal(total)
    shipping = Decimal(shipping)
    order = Order.objects.create(
        user=user,
        customer_email=user.email,
        status=status_,
        channel=channel,
        location=location,
        sold_by=sold_by,
        items_subtotal=total - shipping,
        shipping_cost=shipping,
        total=total,
        tax_rate=Decimal("0.16"),
        tax_amount=services.tax_included(total, Decimal("0.16")),
        needs_shipping=needs_shipping,
        paid_at=paid_at,
        cancelled_at=cancelled_at,
        **extra,
    )
    for product, quantity, subtotal in items:
        subtotal = Decimal(subtotal)
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            product_type=product.product_type,
            unit_price=subtotal / quantity,
            quantity=quantity,
            subtotal=subtotal,
        )
    if created_at:
        Order.objects.filter(pk=order.pk).update(created_at=created_at)
    return order


@override_settings(STORE_TIME_ZONE="America/Mexico_City", COMMISSION_BASE="net")
class ReportTestCase(APITestCase):
    def setUp(self):
        self.customer = make_user("customer@example.com")
        self.warehouse = StockLocation.online_warehouse()
        self.puebla = make_store("Centro", region="Puebla")
        self.merida = make_store("Altabrisa", region="Mérida")
        self.admin = make_staff("boss@example.com", "analytics.view_global_reports")

    def get(self, name, user=None, **params):
        if user is not None:
            self.client.force_authenticate(user)
        return self.client.get(reverse(f"analytics:{name}"), params)


# ---------------------------------------------------------------------------------
# Access and scope
# ---------------------------------------------------------------------------------
class AccessTests(ReportTestCase):
    def test_anonymous_is_rejected(self):
        response = self.get("summary")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_customer_is_forbidden(self):
        response = self.get("summary", self.customer)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_without_report_group_is_forbidden(self):
        response = self.get("summary", make_staff("clerk@example.com"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_branch_permission_without_branches_is_forbidden(self):
        user = make_staff("lost@example.com", "analytics.view_location_reports")
        StaffProfile.objects.create(user=user)
        self.assertEqual(self.get("summary", user).status_code, status.HTTP_403_FORBIDDEN)

    def test_global_and_superuser_see_everything(self):
        make_order(self.customer, paid_at=at(5), location=self.puebla)
        make_order(self.customer, paid_at=at(6), location=self.merida)
        superuser = make_user("root@example.com")
        superuser.is_staff = superuser.is_superuser = True
        superuser.save()
        for user in (self.admin, superuser):
            response = self.get("summary", user, **OCTOBER)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            self.assertEqual(response.data["current"]["orders"], 2)

    def test_group_permission_works(self):
        call_command("setup_order_groups", verbosity=0)
        user = make_staff("grouped@example.com")
        user.groups.add(Group.objects.get(name="Reportes: global"))
        self.assertEqual(self.get("meta", user).status_code, status.HTTP_200_OK)


class ScopeTests(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.manager = make_staff("puebla@example.com", "analytics.view_location_reports")
        StaffProfile.objects.create(user=self.manager).locations.add(self.puebla)
        self.seller = make_staff("seller@example.com", "analytics.view_own_sales")
        self.mine = make_order(
            self.customer,
            total="232.00",
            paid_at=at(3),
            channel=OrderChannel.POS,
            location=self.merida,
            sold_by=self.seller,
        )
        self.puebla_sale = make_order(self.customer, paid_at=at(4), location=self.puebla)
        make_order(self.customer, paid_at=at(5), location=self.warehouse)

    def test_branch_manager_only_sees_assigned_branches(self):
        response = self.get("summary", self.manager, **OCTOBER)
        self.assertEqual(response.data["current"]["orders"], 1)
        self.assertEqual(response.data["current"]["sales_total"], "116.00")

    def test_branch_manager_cannot_pick_another_branch(self):
        response = self.get("summary", self.manager, location=self.merida.pk, **OCTOBER)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_seller_only_sees_own_sales(self):
        response = self.get("summary", self.seller, **OCTOBER)
        self.assertEqual(response.data["current"]["orders"], 1)
        self.assertEqual(response.data["current"]["sales_total"], "232.00")

    def test_seller_cannot_filter_other_sellers(self):
        response = self.get("summary", self.seller, seller=self.manager.pk, **OCTOBER)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_permissions_add_up(self):
        self.manager.user_permissions.add(
            Permission.objects.get(codename="view_own_sales")
        )
        assign_seller(self.mine, self.manager)
        # Fresh instance: Django caches permissions on the user object
        self.manager = type(self.manager).objects.get(pk=self.manager.pk)
        response = self.get("summary", self.manager, **OCTOBER)
        self.assertEqual(response.data["current"]["orders"], 2)

    def test_meta_lists_only_visible_branches(self):
        response = self.get("meta", self.manager)
        self.assertEqual(response.data["scope"]["level"], "location")
        self.assertEqual(
            [loc["id"] for loc in response.data["locations"]], [self.puebla.pk]
        )
        own = self.get("meta", self.seller)
        self.assertEqual(own.data["locations"], [])
        self.assertEqual([s["id"] for s in own.data["sellers"]], [self.seller.pk])

    def test_filters_by_channel_location_and_seller(self):
        self.client.force_authenticate(self.admin)
        pos = self.get("summary", channel="pos", **OCTOBER)
        self.assertEqual(pos.data["current"]["orders"], 1)
        branch = self.get("summary", location=self.puebla.pk, **OCTOBER)
        self.assertEqual(branch.data["current"]["orders"], 1)
        seller = self.get("summary", seller=self.seller.pk, **OCTOBER)
        self.assertEqual(seller.data["current"]["sales_total"], "232.00")


# ---------------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------------
class FilterValidationTests(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.admin)

    def test_bad_values(self):
        for params, field in (
            ({"start": "01/10/2026"}, "start"),
            ({"start": "2026-10-10", "end": "2026-10-01"}, "start"),
            ({"channel": "phone"}, "channel"),
            ({"interval": "hour"}, "interval"),
            ({"location": "abc"}, "location"),
            ({"start": "2026-01-01", "end": "2026-10-31", "interval": "day"}, "interval"),
            ({"start": "2023-01-01", "end": "2026-10-31"}, "start"),
        ):
            with self.subTest(params=params):
                response = self.get("summary", **params)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)

    def test_default_range_is_last_30_days(self):
        response = self.get("summary")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["range"]["days"], 30)
        self.assertEqual(response.data["range"]["interval"], "day")

    def test_interval_follows_range(self):
        response = self.get("timeseries", start="2026-01-01", end="2026-10-31")
        self.assertEqual(response.data["interval"], "week")


# ---------------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------------
class SummaryTests(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.admin)
        product = make_product()
        # October: two sales (one shipped, with shipping), one unpaid, one refunded
        make_order(
            self.customer, total="116.00", paid_at=at(2), items=[(product, 1, "116")]
        )
        make_order(
            self.customer,
            total="282.00",
            shipping="50.00",
            status_=OrderStatus.SHIPPED,
            paid_at=at(20),
            items=[(product, 2, "232")],
        )
        make_order(self.customer, total="999.00", status_=OrderStatus.PENDING_PAYMENT)
        make_order(
            self.customer,
            total="58.00",
            status_=OrderStatus.REFUNDED,
            paid_at=at(10, month=9),
            cancelled_at=at(12),
        )
        # Previous period (Aug 31 - Sep 30)
        make_order(self.customer, total="199.00", paid_at=at(15, month=9))

    def test_totals(self):
        data = self.get("summary", **OCTOBER).data
        current = data["current"]
        self.assertEqual(current["orders"], 2)
        self.assertEqual(current["units"], 3)
        self.assertEqual(current["sales_total"], "398.00")
        self.assertEqual(current["shipping_total"], "50.00")
        self.assertEqual(current["items_total"], "348.00")
        # VAT contained: 16.00 + 38.90
        self.assertEqual(current["tax_total"], "54.90")
        self.assertEqual(current["net_sales"], "343.10")
        self.assertEqual(current["average_ticket"], "199.00")
        self.assertEqual(current["refunds_count"], 1)
        self.assertEqual(current["refunds_total"], "58.00")

    def test_comparison_with_previous_period(self):
        data = self.get("summary", **OCTOBER).data
        self.assertEqual(
            data["previous_range"], {"start": "2026-08-31", "end": "2026-09-30"}
        )
        self.assertEqual(data["previous"]["sales_total"], "199.00")
        self.assertEqual(data["change"]["sales_total"], 100.0)  # 398 vs 199
        self.assertEqual(data["change"]["orders"], 100.0)

    def test_no_previous_sales_gives_no_change(self):
        data = self.get("summary", start="2026-10-01", end="2026-10-02").data
        self.assertIsNone(data["change"]["sales_total"])


class TimeseriesTests(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.admin)

    def test_sales_are_grouped_by_local_day(self):
        # 23:30 in Mexico City on Oct 1 is already Oct 2 in UTC
        make_order(self.customer, paid_at=at(1, hour=23, minute=30))
        make_order(
            self.customer,
            total="50.00",
            paid_at=at(3),
            channel=OrderChannel.POS,
            location=self.puebla,
        )
        data = self.get("timeseries", start="2026-10-01", end="2026-10-03").data
        self.assertEqual(
            [(p["period"], p["total"], p["orders"]) for p in data["points"]],
            [
                ("2026-10-01", "116.00", 1),
                ("2026-10-02", "0.00", 0),
                ("2026-10-03", "50.00", 1),
            ],
        )
        self.assertEqual(data["points"][2]["pos"], "50.00")
        self.assertEqual(data["points"][0]["online"], "116.00")

    def test_weeks_start_on_monday_and_months_fill_gaps(self):
        make_order(self.customer, paid_at=at(8))  # Thursday
        weekly = self.get(
            "timeseries", start="2026-10-01", end="2026-10-14", interval="week"
        ).data
        self.assertEqual(
            [p["period"] for p in weekly["points"]],
            ["2026-09-28", "2026-10-05", "2026-10-12"],
        )
        self.assertEqual(weekly["points"][1]["total"], "116.00")
        monthly = self.get(
            "timeseries", start="2026-08-15", end="2026-10-31", interval="month"
        ).data
        self.assertEqual(
            [p["period"] for p in monthly["points"]],
            ["2026-08-01", "2026-09-01", "2026-10-01"],
        )


class ProductReportTests(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.admin)
        clothes = Category.objects.create(name="Ropa")
        shirts = Category.objects.create(name="Playeras", parent=clothes)
        software = Category.objects.create(name="Software")
        self.shirt = make_product(name="Playera", category=shirts)
        self.hoodie = make_product(name="Sudadera", category=clothes)
        self.license = make_product(
            name="Licencia", product_type="software", stock=0, category=software
        )
        make_order(
            self.customer,
            total="700.00",
            paid_at=at(5),
            items=[(self.shirt, 5, "500"), (self.hoodie, 1, "200")],
        )
        make_order(
            self.customer, total="900.00", paid_at=at(6), items=[(self.license, 1, "900")]
        )

    def test_top_products_by_revenue_and_units(self):
        by_revenue = self.get("products", **OCTOBER).data["results"]
        self.assertEqual(
            [r["name"] for r in by_revenue], ["Licencia", "Playera", "Sudadera"]
        )
        by_units = self.get("products", order_by="units", limit=1, **OCTOBER).data
        self.assertEqual(len(by_units["results"]), 1)
        self.assertEqual(by_units["results"][0]["units"], 5)

    def test_categories_fold_into_parent(self):
        data = self.get("categories", **OCTOBER).data
        self.assertEqual(
            [(c["name"], c["revenue"]) for c in data["categories"]],
            [("Software", "900.00"), ("Ropa", "700.00")],
        )
        leaf = self.get("categories", level="leaf", **OCTOBER).data
        self.assertIn("Ropa / Playeras", [c["name"] for c in leaf["categories"]])
        types = {t["product_type"]: t["revenue"] for t in data["product_types"]}
        self.assertEqual(types, {"software": "900.00", "physical": "700.00"})

    def test_deleted_products_keep_their_sales(self):
        self.hoodie.delete()
        names = [r["name"] for r in self.get("products", **OCTOBER).data["results"]]
        self.assertIn("Sudadera", names)
        categories = self.get("categories", **OCTOBER).data["categories"]
        self.assertIn("Sin categoría", [c["name"] for c in categories])


class BreakdownAndOperationsTests(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.admin)

    def test_breakdown_by_channel_branch_and_region(self):
        make_order(self.customer, total="100.00", paid_at=at(2), location=self.warehouse)
        make_order(
            self.customer,
            total="300.00",
            paid_at=at(3),
            channel=OrderChannel.POS,
            location=self.puebla,
        )
        make_order(
            self.customer,
            total="50.00",
            paid_at=at(4),
            channel=OrderChannel.POS,
            location=self.merida,
        )
        data = self.get("breakdown", **OCTOBER).data
        self.assertEqual(
            [(c["channel"], c["total"]) for c in data["channels"]],
            [("pos", "350.00"), ("online", "100.00")],
        )
        self.assertEqual(data["locations"][0]["name"], "Centro")
        self.assertEqual(
            [r["region"] for r in data["regions"]], ["Puebla", "En línea", "Mérida"]
        )

    def test_operations(self):
        created = at(5)
        make_order(self.customer, paid_at=at(5), created_at=created, needs_shipping=True)
        make_order(self.customer, status_=OrderStatus.EXPIRED, created_at=created)
        make_order(self.customer, status_=OrderStatus.PENDING_PAYMENT, created_at=created)
        make_order(
            self.customer,
            status_=OrderStatus.DELIVERED,
            paid_at=at(5),
            created_at=created,
            cancel_requested_at=at(6),
        )
        data = self.get("operations", **OCTOBER).data
        counts = {s["status"]: s["count"] for s in data["statuses"]}
        self.assertEqual(counts["paid"], 1)
        self.assertEqual(counts["expired"], 1)
        self.assertEqual(data["created"], 4)
        self.assertEqual(data["paid_rate"], 50.0)
        self.assertEqual(
            data["backlog"],
            {"awaiting_payment": 1, "to_ship": 1, "cancellation_requests": 1},
        )


# ---------------------------------------------------------------------------------
# Commissions
# ---------------------------------------------------------------------------------
@override_settings(COMMISSION_BASE="net")
class CommissionTests(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.seller = make_staff("seller@example.com", "analytics.view_own_sales")
        self.profile = StaffProfile.objects.create(
            user=self.seller, commission_percent=Decimal(10)
        )

    def pos_sale(self, total="116.00", **kwargs):
        order = make_order(
            self.customer,
            total=total,
            channel=OrderChannel.POS,
            location=self.puebla,
            paid_at=kwargs.pop("paid_at", at(5)),
            **kwargs,
        )
        return assign_seller(order, self.seller)

    def test_net_base_excludes_vat_and_shipping(self):
        order = make_order(self.customer, total="166.00", shipping="50.00")
        self.assertEqual(commission_for(order, Decimal(10)), Decimal("10.00"))
        with self.settings(COMMISSION_BASE="total"):
            self.assertEqual(commission_for(order, Decimal(10)), Decimal("16.60"))

    def test_percentage_is_frozen_on_the_sale(self):
        order = self.pos_sale()
        self.assertEqual(order.commission_percent, Decimal("10.00"))
        self.assertEqual(order.commission_amount, Decimal("10.00"))
        self.profile.commission_percent = Decimal(20)
        self.profile.save()
        order.refresh_from_db()
        self.assertEqual(order.commission_amount, Decimal("10.00"))
        # Re-applying (admin action) takes the new percentage
        self.seller.refresh_from_db()
        order = assign_seller(order, self.seller)
        self.assertEqual(order.commission_amount, Decimal("20.00"))

    def test_seller_without_percentage_earns_nothing(self):
        self.profile.commission_percent = None
        self.profile.save()
        self.seller.refresh_from_db()
        order = self.pos_sale()
        self.assertIsNone(order.commission_percent)
        self.assertEqual(order.commission_amount, Decimal("0.00"))

    def test_only_staff_can_be_credited(self):
        order = make_order(self.customer)
        with self.assertRaises(ValueError):
            assign_seller(order, self.customer)

    def test_report_excludes_refunds(self):
        self.pos_sale()
        self.pos_sale(total="232.00")
        refunded = self.pos_sale(total="1160.00")
        Order.objects.filter(pk=refunded.pk).update(
            status=OrderStatus.REFUNDED, cancelled_at=at(6)
        )
        data = self.get("commissions", self.admin, **OCTOBER).data
        self.assertEqual(len(data["results"]), 1)
        row = data["results"][0]
        self.assertEqual(row["orders"], 2)
        self.assertEqual(row["sales_total"], "348.00")
        self.assertEqual(row["commission"], "30.00")
        self.assertEqual(row["current_percent"], "10.00")
        self.assertEqual(data["totals"]["commission"], "30.00")
        self.assertEqual(data["base"], "net")
        summary = self.get("summary", **OCTOBER).data
        self.assertEqual(summary["current"]["commission_total"], "30.00")

    def test_seller_sees_own_commission(self):
        self.pos_sale()
        other = make_staff("other@example.com")
        assign_seller(make_order(self.customer, paid_at=at(5)), other)
        data = self.get("commissions", self.seller, **OCTOBER).data
        self.assertEqual([r["seller_id"] for r in data["results"]], [self.seller.pk])


# ---------------------------------------------------------------------------------
# Checkout records the online warehouse; groups command
# ---------------------------------------------------------------------------------
class CheckoutLocationTests(TestCase):
    def test_online_orders_are_recorded_in_the_online_warehouse(self):
        user = make_user("buyer@example.com")
        product = make_product(product_type="software", stock=0)
        cart = Cart.for_user(user)
        CartItem.objects.create(cart=cart, product=product, quantity=1)
        order = services.create_order_from_cart(user)
        self.assertEqual(order.channel, OrderChannel.ONLINE)
        self.assertEqual(order.location, StockLocation.online_warehouse())
        self.assertIsNone(order.sold_by)

    def test_groups_command_creates_report_groups(self):
        call_command("setup_order_groups", verbosity=0)
        for name in (
            "Reportes: global",
            "Reportes: sucursal",
            "Reportes: mis ventas",
            "Comisiones: configuración",
            "Sucursales: configuración",
        ):
            self.assertTrue(Group.objects.filter(name=name).exists(), name)
