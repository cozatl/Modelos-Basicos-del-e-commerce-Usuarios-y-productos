from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from products.models import Product

from .models import Cart, CartItem

User = get_user_model()


def make_user(email):
    return User.objects.create_user(email=email, password="test-pass-12345")


_counter = 0


def make_product(**overrides):
    """A physical product for sale by default; override fields per test."""
    global _counter
    _counter += 1
    data = {
        "name": f"Product {_counter}",
        "slug": f"product-{_counter}",
        "sku": f"SKU-{_counter}",
        "description": "",
        "product_type": "physical",
        "access_type": "purchase",
        "price": Decimal("100.00"),
        "discount_price": None,
        "stock": 5,
        "is_active": True,
    }
    data.update(overrides)
    return Product.objects.create(**data)


class CartTestCase(APITestCase):
    def setUp(self):
        self.user = make_user("buyer@example.com")
        self.other = make_user("other@example.com")
        self.client.force_authenticate(self.user)
        self.cart_url = reverse("orders:cart")
        self.items_url = reverse("orders:cart-items")

    def item_url(self, pk):
        return reverse("orders:cart-item-detail", kwargs={"pk": pk})

    def add(self, product, quantity=None):
        payload = {"product": product.slug}
        if quantity is not None:
            payload["quantity"] = quantity
        return self.client.post(self.items_url, payload, format="json")


class CartAccessTests(CartTestCase):
    def test_requires_authentication(self):
        self.client.force_authenticate(None)
        for response in (
            self.client.get(self.cart_url),
            self.client.post(self.items_url, {}, format="json"),
            self.client.delete(self.cart_url),
        ):
            self.assertIn(
                response.status_code,
                (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
            )

    def test_get_creates_an_empty_cart_on_the_fly(self):
        self.assertFalse(Cart.objects.filter(user=self.user).exists())
        response = self.client.get(self.cart_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["items"], [])
        self.assertEqual(response.data["total"], "0.00")
        self.assertEqual(response.data["total_quantity"], 0)
        self.assertTrue(Cart.objects.filter(user=self.user).exists())

    def test_cart_owner_comes_from_the_request_not_the_payload(self):
        product = make_product()
        response = self.client.post(
            self.items_url,
            {"product": product.slug, "user": self.other.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(CartItem.objects.filter(cart__user=self.user).exists())
        self.assertFalse(CartItem.objects.filter(cart__user=self.other).exists())

    def test_each_user_only_sees_their_own_cart(self):
        mine, theirs = make_product(), make_product()
        self.add(mine)
        self.client.force_authenticate(self.other)
        self.add(theirs)

        response = self.client.get(self.cart_url)
        self.assertEqual(
            [i["product"]["slug"] for i in response.data["items"]], [theirs.slug]
        )

    def test_cannot_change_or_delete_another_users_item(self):
        product = make_product()
        other_cart = Cart.for_user(self.other)
        item = CartItem.objects.create(cart=other_cart, product=product, quantity=1)

        response = self.client.patch(self.item_url(item.pk), {"quantity": 2}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        response = self.client.delete(self.item_url(item.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        item.refresh_from_db()
        self.assertEqual(item.quantity, 1)

    def test_clearing_my_cart_does_not_touch_other_carts(self):
        product = make_product()
        CartItem.objects.create(cart=Cart.for_user(self.other), product=product)
        self.add(product)

        response = self.client.delete(self.cart_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["items"], [])
        self.assertEqual(CartItem.objects.filter(cart__user=self.other).count(), 1)


class AddToCartTests(CartTestCase):
    def test_add_physical_product_returns_full_cart_with_totals(self):
        product = make_product(price=Decimal("100.00"), discount_price=Decimal("80.00"))
        response = self.add(product, 2)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        item = response.data["items"][0]
        self.assertEqual(item["quantity"], 2)
        self.assertEqual(item["unit_price"], "80.00")  # discount wins
        self.assertEqual(item["subtotal"], "160.00")
        self.assertEqual(item["max_quantity"], 5)
        self.assertIsNone(item["problem"])
        self.assertEqual(response.data["total"], "160.00")
        self.assertEqual(response.data["total_quantity"], 2)

    def test_adding_the_same_product_again_merges_the_row(self):
        product = make_product(stock=5)
        self.add(product, 2)
        response = self.add(product, 1)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["items"]), 1)
        self.assertEqual(response.data["items"][0]["quantity"], 3)

    def test_quantity_defaults_to_one(self):
        response = self.add(make_product())
        self.assertEqual(response.data["items"][0]["quantity"], 1)

    def test_cannot_exceed_stock(self):
        product = make_product(stock=3)
        response = self.add(product, 4)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("quantity", response.data)

    def test_cannot_exceed_stock_when_merging(self):
        product = make_product(stock=3)
        self.add(product, 2)
        response = self.add(product, 2)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(CartItem.objects.get(product=product).quantity, 2)

    def test_out_of_stock_physical_product_is_rejected(self):
        response = self.add(make_product(stock=0))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_digital_products_max_one(self):
        for product_type in ("software", "ebook", "video"):
            with self.subTest(product_type=product_type):
                product = make_product(product_type=product_type, stock=0)
                self.assertEqual(
                    self.add(product, 2).status_code, status.HTTP_400_BAD_REQUEST
                )
                response = self.add(product, 1)
                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                row = next(
                    i for i in response.data["items"] if i["product"]["slug"] == product.slug
                )
                self.assertEqual(row["max_quantity"], 1)
                # Adding it a second time would make 2
                self.assertEqual(self.add(product).status_code, status.HTTP_400_BAD_REQUEST)

    def test_inactive_product_is_rejected(self):
        response = self.add(make_product(is_active=False))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_subscription_and_free_products_are_rejected(self):
        for access_type in ("subscription", "free"):
            with self.subTest(access_type=access_type):
                product = make_product(
                    product_type="video", access_type=access_type, price=None, stock=0
                )
                self.assertEqual(self.add(product).status_code, status.HTTP_400_BAD_REQUEST)

    def test_unknown_product_is_rejected(self):
        response = self.client.post(self.items_url, {"product": "nope"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("product", response.data)

    def test_zero_or_negative_quantity_is_rejected(self):
        product = make_product()
        for quantity in (0, -1):
            with self.subTest(quantity=quantity):
                self.assertEqual(
                    self.add(product, quantity).status_code, status.HTTP_400_BAD_REQUEST
                )


class UpdateCartItemTests(CartTestCase):
    def setUp(self):
        super().setUp()
        self.product = make_product(stock=4)
        response = self.add(self.product, 1)
        self.item_id = response.data["items"][0]["id"]

    def test_patch_sets_the_quantity(self):
        response = self.client.patch(self.item_url(self.item_id), {"quantity": 4}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["items"][0]["quantity"], 4)
        self.assertEqual(response.data["total"], "400.00")

    def test_patch_cannot_exceed_stock(self):
        response = self.client.patch(self.item_url(self.item_id), {"quantity": 5}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_zero_is_rejected_use_delete(self):
        response = self.client.patch(self.item_url(self.item_id), {"quantity": 0}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_digital_above_one_is_rejected(self):
        ebook = make_product(product_type="ebook", stock=0)
        item_id = next(
            i["id"] for i in self.add(ebook).data["items"] if i["product"]["slug"] == ebook.slug
        )
        response = self.client.patch(self.item_url(item_id), {"quantity": 2}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_item(self):
        response = self.client.delete(self.item_url(self.item_id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["items"], [])
        self.assertFalse(CartItem.objects.filter(pk=self.item_id).exists())

    def test_clear_cart(self):
        self.add(make_product())
        response = self.client.delete(self.cart_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["items"], [])
        self.assertEqual(response.data["total"], "0.00")


class PriceIsNotStoredTests(CartTestCase):
    def test_total_follows_the_current_product_price(self):
        product = make_product(price=Decimal("100.00"))
        self.add(product, 2)

        product.price = Decimal("120.00")
        product.save()
        response = self.client.get(self.cart_url)
        self.assertEqual(response.data["total"], "240.00")

        product.discount_price = Decimal("90.00")
        product.save()
        response = self.client.get(self.cart_url)
        self.assertEqual(response.data["total"], "180.00")

    def test_items_that_became_unbuyable_are_flagged(self):
        product = make_product(stock=5)
        self.add(product, 3)

        product.stock = 2
        product.save()
        response = self.client.get(self.cart_url)
        self.assertTrue(response.data["has_problems"])
        self.assertIsNotNone(response.data["items"][0]["problem"])

        product.stock = 5
        product.is_active = False
        product.save()
        response = self.client.get(self.cart_url)
        self.assertTrue(response.data["has_problems"])

    def test_lowering_quantity_to_stock_clears_the_problem(self):
        product = make_product(stock=5)
        item_id = self.add(product, 3).data["items"][0]["id"]
        product.stock = 2
        product.save()

        response = self.client.patch(self.item_url(item_id), {"quantity": 2}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["has_problems"])


class OrderEndpointTests(CartTestCase):
    def test_orders_are_read_only_until_m4(self):
        cart = Cart.for_user(self.other)
        response = self.client.post("/api/v1/orders/", {"cart": cart.pk}, format="json")
        self.assertIn(
            response.status_code,
            (status.HTTP_405_METHOD_NOT_ALLOWED, status.HTTP_404_NOT_FOUND),
        )
