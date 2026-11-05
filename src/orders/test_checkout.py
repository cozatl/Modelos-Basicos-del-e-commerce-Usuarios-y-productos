"""M4: checkout, stock reservation, payments, cancellations,
and staff actions."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Permission
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from addresses.tests import configure_shipping, make_address
from billing.models import Payment, PaymentStatus

from . import services
from .admin import OrderAdmin
from .choices import OrderStatus
from .models import Cart, CartItem, Order
from .tests import make_product, make_user


def fill_cart(user, *lines):
    cart = Cart.for_user(user)
    for product, quantity in lines:
        CartItem.objects.create(cart=cart, product=product, quantity=quantity)
    return cart


# Fixed here so the tests do not depend on the .env (e.g. 1 minute for manual testing)
@override_settings(
    ORDER_RESERVATION_MINUTES=10,
    TAX_RATE=Decimal("0.16"),
    PAYMENTS_SIMULATED=True,
)
class CheckoutTestCase(APITestCase):
    def setUp(self):
        # Zone 1 = 01000 free · Zone 2 = 02000 $50 · default $150
        configure_shipping()
        self.user = make_user("buyer@example.com")
        self.other = make_user("other@example.com")
        self.client.force_authenticate(self.user)
        self.address = make_address(self.user, postal_code="02000")
        self.orders_url = reverse("order-list")

    def order_url(self, order, suffix=""):
        name = {
            "": "order-detail",
            "pay": "order-pay",
            "cancel": "order-cancel",
        }[suffix]
        order_id = order.order_id if hasattr(order, "order_id") else order
        return reverse(name, kwargs={"order_id": order_id})

    def place(self, address="default", **extra):
        payload = dict(extra)
        if address == "default":
            payload["address"] = self.address.pk
        elif address is not None:
            payload["address"] = address
        return self.client.post(self.orders_url, payload, format="json")


class QuoteTests(CheckoutTestCase):
    def test_quote_with_address(self):
        fill_cart(self.user, (make_product(price=Decimal("100.00")), 2))
        response = self.client.get(
            reverse("orders:checkout-quote"), {"address": self.address.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["items_subtotal"], "200.00")
        self.assertEqual(response.data["shipping_cost"], "50.00")
        self.assertEqual(response.data["shipping_zone_name"], "Zona 2")
        self.assertEqual(response.data["total"], "250.00")
        self.assertEqual(response.data["tax_amount"], "34.48")  # 250 - 250/1.16
        self.assertEqual(response.data["reservation_minutes"], 10)

    def test_quote_with_unlisted_postal_code_uses_default_zone(self):
        fill_cart(self.user, (make_product(), 1))
        response = self.client.get(
            reverse("orders:checkout-quote"), {"postal_code": "99999"}
        )
        self.assertEqual(response.data["shipping_cost"], "150.00")

    def test_quote_without_postal_code_for_physical_products(self):
        fill_cart(self.user, (make_product(), 1))
        response = self.client.get(reverse("orders:checkout-quote"))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("address", response.data)

    def test_quote_digital_only_needs_no_address(self):
        fill_cart(self.user, (make_product(product_type="software", stock=0), 1))
        response = self.client.get(reverse("orders:checkout-quote"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["needs_shipping"])
        self.assertEqual(response.data["shipping_cost"], "0.00")


class CreateOrderTests(CheckoutTestCase):
    def test_cart_becomes_a_pending_order_and_stock_is_reserved(self):
        shirt = make_product(
            price=Decimal("200.00"), discount_price=Decimal("150.00"), stock=5
        )
        licence = make_product(product_type="software", price=Decimal("500.00"), stock=0)
        fill_cart(self.user, (shirt, 2), (licence, 1))

        before = timezone.now()
        response = self.place(notes="  Tocar el timbre  ")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        data = response.data

        self.assertEqual(data["status"], "pending_payment")
        self.assertEqual(data["items_subtotal"], "800.00")  # 2 x 150 + 500
        self.assertEqual(data["shipping_cost"], "50.00")
        self.assertEqual(data["total"], "850.00")
        self.assertEqual(data["tax_rate"], "0.1600")
        self.assertEqual(data["tax_amount"], "117.24")
        self.assertEqual(data["subtotal_before_tax"], "732.76")
        self.assertTrue(data["needs_shipping"])
        self.assertEqual(data["notes"], "Tocar el timbre")
        self.assertEqual(data["shipping_address"]["postal_code"], "02000")
        self.assertTrue(data["can_pay"])
        self.assertTrue(0 < data["expires_in_seconds"] <= 600)
        self.assertEqual(
            [(i["product_name"], i["unit_price"], i["quantity"]) for i in data["items"]],
            [(shirt.name, "150.00", 2), (licence.name, "500.00", 1)],
        )

        order = Order.objects.get(order_id=data["order_id"])
        self.assertEqual(order.user, self.user)
        self.assertEqual(order.customer_email, self.user.email)
        self.assertAlmostEqual(
            order.expires_at,
            before + timedelta(minutes=10),
            delta=timedelta(seconds=5),
        )
        shirt.refresh_from_db()
        self.assertEqual(shirt.stock, 3)  # reserved
        # cart emptied
        self.assertFalse(Cart.for_user(self.user).items.exists())

    def test_order_keeps_its_prices_and_address_after_changes(self):
        product = make_product(price=Decimal("100.00"))
        fill_cart(self.user, (product, 1))
        order_id = self.place().data["order_id"]

        product.price = Decimal("999.00")
        product.save()
        self.address.street = "Otra calle"
        self.address.save()

        data = self.client.get(self.order_url(order_id)).data
        self.assertEqual(data["items"][0]["unit_price"], "100.00")
        self.assertEqual(data["shipping_address"]["street"], "Av. Juárez")

    def test_physical_products_require_an_address(self):
        fill_cart(self.user, (make_product(), 1))
        response = self.place(address=None)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("address", response.data)
        self.assertFalse(Order.objects.exists())

    def test_digital_only_order_without_address(self):
        fill_cart(self.user, (make_product(product_type="ebook", stock=0), 1))
        response = self.place(address=None)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertFalse(response.data["needs_shipping"])
        self.assertEqual(response.data["shipping_cost"], "0.00")
        self.assertIsNone(response.data["shipping_address"])

    def test_cannot_use_another_users_address(self):
        fill_cart(self.user, (make_product(), 1))
        theirs = make_address(self.other)
        response = self.place(address=theirs.pk)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("address", response.data)

    def test_empty_cart(self):
        response = self.place()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cart", response.data)

    def test_stock_that_changed_after_adding_blocks_the_order(self):
        product = make_product(stock=5)
        fill_cart(self.user, (product, 4))
        product.stock = 2
        product.save()

        response = self.place()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(product.slug, response.data["problems"])
        product.refresh_from_db()
        self.assertEqual(product.stock, 2)  # nothing reserved
        # cart untouched
        self.assertTrue(Cart.for_user(self.user).items.exists())

    def test_free_shipping_threshold_is_applied(self):
        configure_shipping(threshold=Decimal(500))
        fill_cart(self.user, (make_product(price=Decimal("600.00")), 1))
        data = self.place().data
        self.assertEqual(data["shipping_cost"], "0.00")
        self.assertTrue(data["free_shipping_applied"])

    def test_list_and_other_users_orders(self):
        fill_cart(self.user, (make_product(), 1))
        order_id = self.place().data["order_id"]

        listing = self.client.get(self.orders_url).data
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["results"][0]["order_id"], order_id)
        self.assertEqual(listing["results"][0]["item_count"], 1)

        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.orders_url).data["count"], 0)
        self.assertEqual(
            self.client.get(self.order_url(order_id)).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.post(self.order_url(order_id, "pay")).status_code,
            status.HTTP_404_NOT_FOUND,
        )


class PaymentTests(CheckoutTestCase):
    def setUp(self):
        super().setUp()
        self.product = make_product(price=Decimal("116.00"), stock=3)
        fill_cart(self.user, (self.product, 1))
        self.order_id = self.place().data["order_id"]

    def test_approved_payment_marks_the_order_paid_and_sends_the_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.order_url(self.order_id, "pay"), {}, format="json"
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["status"], "paid")
        self.assertIsNotNone(response.data["paid_at"])
        self.assertFalse(response.data["can_pay"])
        self.assertEqual(response.data["payments"][0]["status"], "approved")

        payment = Payment.objects.get()
        # 116 + 50 shipping
        self.assertEqual(payment.amount, Decimal("166.00"))
        self.assertEqual(payment.provider, "simulated")

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, [self.user.email])
        self.assertIn(self.order_id, email.subject)
        self.assertIn("Incluye IVA 16%", email.body)

    def test_declined_payment_keeps_the_order_pending(self):
        response = self.client.post(
            self.order_url(self.order_id, "pay"),
            {"outcome": "declined"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("payment", response.data)
        order = Order.objects.get()
        self.assertEqual(order.status, OrderStatus.PENDING_PAYMENT)
        self.assertEqual(Payment.objects.get().status, PaymentStatus.DECLINED)
        # Retry works
        response = self.client.post(
            self.order_url(self.order_id, "pay"), {}, format="json"
        )
        self.assertEqual(response.data["status"], "paid")

    def test_cannot_pay_twice(self):
        self.client.post(self.order_url(self.order_id, "pay"), {}, format="json")
        response = self.client.post(
            self.order_url(self.order_id, "pay"), {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "not_payable")

    def test_paying_after_expiration_releases_stock_and_returns_items_to_cart(
        self,
    ):
        Order.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.client.post(
            self.order_url(self.order_id, "pay"), {}, format="json"
        )
        # The list/detail safety net may have expired it first; both paths
        # answer 409.
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        order = Order.objects.get()
        self.assertEqual(order.status, OrderStatus.EXPIRED)
        self.assertTrue(order.stock_released)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)
        self.assertEqual(Cart.for_user(self.user).items.get().product, self.product)
        self.assertFalse(Payment.objects.exists())

    @override_settings(PAYMENTS_SIMULATED=False)
    def test_without_provider(self):
        response = self.client.post(
            self.order_url(self.order_id, "pay"), {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)


class ExpirationTests(CheckoutTestCase):
    def test_periodic_release_only_touches_overdue_pending_orders(self):
        product = make_product(stock=10)
        fill_cart(self.user, (product, 2))
        overdue = Order.objects.get(order_id=self.place().data["order_id"])
        fill_cart(self.user, (product, 3))
        fresh = Order.objects.get(order_id=self.place().data["order_id"])
        fill_cart(self.user, (product, 1))
        paid = Order.objects.get(order_id=self.place().data["order_id"])
        services.pay_order(paid)
        product.refresh_from_db()
        self.assertEqual(product.stock, 4)

        Order.objects.filter(pk__in=[overdue.pk, paid.pk]).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        self.assertEqual(services.release_expired_orders(), 1)
        self.assertEqual(services.release_expired_orders(), 0)  # idempotent

        overdue.refresh_from_db()
        fresh.refresh_from_db()
        paid.refresh_from_db()
        self.assertEqual(overdue.status, OrderStatus.EXPIRED)
        self.assertEqual(fresh.status, OrderStatus.PENDING_PAYMENT)
        self.assertEqual(paid.status, OrderStatus.PAID)
        product.refresh_from_db()
        self.assertEqual(product.stock, 6)

    def test_celery_task_and_management_command(self):
        from django.core.management import call_command

        from .tasks import release_expired_orders_task

        fill_cart(self.user, (make_product(), 1))
        self.place()
        Order.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(release_expired_orders_task.apply().get(), 1)
        call_command("release_expired_orders", verbosity=0)

    def test_listing_expires_overdue_orders_on_the_fly(self):
        fill_cart(self.user, (make_product(), 1))
        self.place()
        Order.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        listing = self.client.get(self.orders_url).data
        self.assertEqual(listing["results"][0]["status"], "expired")


class CustomerCancelTests(CheckoutTestCase):
    def setUp(self):
        super().setUp()
        self.product = make_product(stock=4)
        fill_cart(self.user, (self.product, 2))
        self.order = Order.objects.get(order_id=self.place().data["order_id"])

    def test_unpaid_order_is_cancelled_right_away(self):
        response = self.client.post(
            self.order_url(self.order, "cancel"),
            {"reason": "Me equivoqué"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "cancelled")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 4)

    def test_paid_order_only_gets_a_request(self):
        services.pay_order(self.order)
        response = self.client.post(
            self.order_url(self.order, "cancel"),
            {"reason": "Ya no lo quiero"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "paid")
        self.assertIsNotNone(response.data["cancel_requested_at"])
        self.assertFalse(response.data["can_request_cancellation"])
        self.product.refresh_from_db()
        # still reserved until staff decides
        self.assertEqual(self.product.stock, 2)

        again = self.client.post(
            self.order_url(self.order, "cancel"),
            {},
            format="json",
        )
        self.assertEqual(again.status_code, status.HTTP_409_CONFLICT)


class StaffActionTests(CheckoutTestCase):
    def setUp(self):
        super().setUp()
        self.product = make_product(stock=5)
        fill_cart(self.user, (self.product, 2))
        self.order = Order.objects.get(order_id=self.place().data["order_id"])
        services.pay_order(self.order)
        self.order.refresh_from_db()

    def stock(self):
        self.product.refresh_from_db()
        return self.product.stock

    def test_ship_and_deliver(self):
        services.mark_shipped(self.order)
        services.mark_delivered(self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.DELIVERED)
        self.assertIsNotNone(self.order.shipped_at)
        self.assertIn("delivered", self.order.staff_notes)

    def test_cannot_deliver_before_shipping(self):
        with self.assertRaises(services.CheckoutError):
            services.mark_delivered(self.order)

    def test_refund_with_restock(self):
        self.assertEqual(self.stock(), 3)
        services.approve_cancellation(self.order, restock=True)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.REFUNDED)
        self.assertEqual(self.stock(), 5)
        self.assertEqual(Payment.objects.get().status, PaymentStatus.REFUNDED)

    def test_refund_without_restock(self):
        services.approve_cancellation(self.order, restock=False)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.REFUNDED)
        self.assertEqual(self.stock(), 3)

    def test_reject_request(self):
        services.customer_cancel(self.order, "Cambio de opinión")
        services.reject_cancellation(self.order)
        self.order.refresh_from_db()
        self.assertIsNone(self.order.cancel_requested_at)
        self.assertEqual(self.order.status, OrderStatus.PAID)
        self.assertIn("rejected", self.order.staff_notes)


class AdminPermissionTests(TestCase):
    def actions_for(self, user):
        request = RequestFactory().get("/admin/orders/order/")
        request.user = user
        return set(OrderAdmin(Order, AdminSite()).get_actions(request))

    def test_actions_depend_on_custom_permissions(self):
        staff = make_user("staff@example.com")
        staff.is_staff = True
        staff.save()
        staff.user_permissions.add(Permission.objects.get(codename="view_order"))
        staff = type(staff).objects.get(pk=staff.pk)  # reset permission cache
        self.assertFalse(
            {"action_mark_shipped", "action_refund_restock"} & self.actions_for(staff)
        )

        staff.user_permissions.add(Permission.objects.get(codename="change_order_status"))
        staff = type(staff).objects.get(pk=staff.pk)
        actions = self.actions_for(staff)
        self.assertIn("action_mark_shipped", actions)
        self.assertNotIn("action_refund_restock", actions)

        staff.user_permissions.add(
            Permission.objects.get(codename="approve_order_cancellation")
        )
        staff = type(staff).objects.get(pk=staff.pk)
        self.assertIn("action_refund_restock", self.actions_for(staff))

    def test_setup_order_groups_command(self):
        from django.contrib.auth.models import Group
        from django.core.management import call_command

        call_command("setup_order_groups", verbosity=0)
        group = Group.objects.get(name="Pedidos: envíos")
        self.assertTrue(group.permissions.filter(codename="change_order_status").exists())
