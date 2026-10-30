from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from products.choices import AccessType, ProductType
from products.models import Product

PASSWORD = "pass-12345"


def make_user(email, **extra):
    return get_user_model().objects.create_user(
        email=email, password=PASSWORD, full_name="Test User", **extra
    )


def make_product(name, **extra):
    data = {"description": "Desc", "price": Decimal("100.00"), "stock": 5}
    data.update(extra)
    return Product.objects.create(name=name, **data)


class ProductModelTest(TestCase):
    """Model and business rules."""

    @classmethod
    def setUpTestData(cls):
        cls.product = make_product(
            "Smartphone XYZ", price=Decimal("299.99"), stock=50
        )

    def test_product_creation(self):
        self.assertEqual(self.product.price, Decimal("299.99"))
        self.assertEqual(self.product.product_type, ProductType.PHYSICAL)
        self.assertEqual(self.product.access_type, AccessType.PURCHASE)
        self.assertTrue(self.product.is_physical)
        self.assertTrue(self.product.is_available)

    def test_product_string_representation(self):
        self.assertEqual(str(self.product), "Smartphone XYZ - $299.99")

    def test_product_price_positive(self):
        product = Product(name="Bad", description="x", price=Decimal(-10))
        with self.assertRaises(ValidationError):
            product.full_clean()

    def test_physical_cannot_be_subscription(self):
        product = Product(
            name="Bad",
            description="x",
            product_type=ProductType.PHYSICAL,
            access_type=AccessType.SUBSCRIPTION,
        )
        with self.assertRaises(ValidationError) as ctx:
            product.full_clean()
        self.assertIn("access_type", ctx.exception.message_dict)

    def test_purchase_requires_price(self):
        product = Product(name="Bad", description="x", price=None)
        with self.assertRaises(ValidationError) as ctx:
            product.full_clean()
        self.assertIn("price", ctx.exception.message_dict)

    def test_software_license_is_valid_digital_purchase(self):
        product = Product(
            name="Antivirus 1 año",
            description="Licencia",
            product_type=ProductType.SOFTWARE,
            price=Decimal("499.00"),
            stock=0,
        )
        product.full_clean()  # must not raise
        self.assertFalse(product.is_physical)
        self.assertTrue(product.is_available)

    def test_digital_rejects_stock_and_weight(self):
        product = Product(
            name="Bad",
            description="x",
            product_type=ProductType.SOFTWARE,
            price=Decimal(10),
            stock=3,
            weight=Decimal("1.5"),
        )
        with self.assertRaises(ValidationError) as ctx:
            product.full_clean()
        self.assertIn("stock", ctx.exception.message_dict)
        self.assertIn("weight", ctx.exception.message_dict)

    def test_subscription_has_no_price(self):
        product = Product(
            name="Curso",
            description="x",
            product_type=ProductType.VIDEO,
            access_type=AccessType.SUBSCRIPTION,
            price=Decimal(99),
        )
        with self.assertRaises(ValidationError) as ctx:
            product.full_clean()
        self.assertIn("price", ctx.exception.message_dict)

    def test_out_of_stock_physical_not_available(self):
        product = make_product("Agotado", stock=0)
        self.assertFalse(product.is_available)


@override_settings(PRODUCTS_PREVIEW_LIMIT=8)
class ProductApiTest(TestCase):
    """Permissions, pagination and preview endpoint."""

    list_url = "/api/v1/products/"
    preview_url = "/api/v1/products/preview/"

    @classmethod
    def setUpTestData(cls):
        cls.customer = make_user("cliente@test.com")
        cls.manager = make_user("catalogo@test.com", is_staff=True)
        group = Group.objects.create(name="Catálogo")
        group.permissions.set(
            Permission.objects.filter(
                content_type__app_label="products",
                codename__in=(
                    "add_product",
                    "change_product",
                    "delete_product",
                ),
            )
        )
        cls.manager.groups.add(group)

        for i in range(10):
            make_product(f"Producto {i}")
        cls.featured = make_product("Destacado", is_featured=True)
        cls.inactive = make_product("Inactivo", is_active=False)

    def setUp(self):
        cache.clear()  # reset throttling counters
        self.client = APIClient()

    # --- Permissions -------------------------------------------------
    def test_anonymous_cannot_list(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 401)

    def test_customer_can_list_paginated(self):
        self.client.force_authenticate(self.customer)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 3)
        # 11 active products; the inactive one is hidden
        self.assertEqual(response.data["count"], 11)

    def test_page_size_param(self):
        self.client.force_authenticate(self.customer)
        response = self.client.get(self.list_url, {"page_size": 6})
        self.assertEqual(len(response.data["results"]), 6)

    def test_customer_cannot_create(self):
        self.client.force_authenticate(self.customer)
        response = self.client.post(
            self.list_url,
            {"name": "Hack", "description": "x", "price": "1.00"},
        )
        self.assertEqual(response.status_code, 403)

    def test_customer_cannot_delete(self):
        self.client.force_authenticate(self.customer)
        response = self.client.delete(f"{self.list_url}{self.featured.slug}/")
        self.assertEqual(response.status_code, 403)

    def test_manager_can_create(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(
            self.list_url,
            {
                "name": "Licencia Office",
                "description": "Software",
                "product_type": ProductType.SOFTWARE,
                "price": "999.00",
            },
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data["is_physical"])

    def test_api_rejects_invalid_combination(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(
            self.list_url,
            {
                "name": "Playera",
                "description": "x",
                "product_type": ProductType.PHYSICAL,
                "access_type": AccessType.SUBSCRIPTION,
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_customer_cannot_see_inactive(self):
        self.client.force_authenticate(self.customer)
        response = self.client.get(f"{self.list_url}{self.inactive.slug}/")
        self.assertEqual(response.status_code, 404)

    def test_filter_by_type(self):
        make_product("Licencia", product_type=ProductType.SOFTWARE, stock=0)
        self.client.force_authenticate(self.customer)
        response = self.client.get(self.list_url, {"type": "software"})
        self.assertEqual(response.data["count"], 1)

    # --- Preview -----------------------------------------------------
    def test_preview_is_public_and_limited(self):
        response = self.client.get(self.preview_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 8)

    def test_preview_puts_featured_first(self):
        response = self.client.get(self.preview_url)
        self.assertEqual(response.data[0]["name"], "Destacado")

    def test_preview_hides_private_fields(self):
        response = self.client.get(self.preview_url)
        for field in ("description", "stock", "sku"):
            self.assertNotIn(field, response.data[0])

    def test_preview_ignores_invalid_token(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer expired.token")
        response = self.client.get(self.preview_url)
        self.assertEqual(response.status_code, 200)


class CategoryTest(TestCase):
    """Categories: two levels, public read, filter includes subcategories."""

    categories_url = "/api/v1/categories/"
    products_url = "/api/v1/products/"

    @classmethod
    def setUpTestData(cls):
        from products.models import Category

        cls.Category = Category
        cls.customer = make_user("cliente2@test.com")
        cls.phones = Category.objects.create(name="Celulares")
        cls.cases = Category.objects.create(name="Fundas", parent=cls.phones)
        cls.software = Category.objects.create(name="Software")
        Category.objects.create(name="Oculta", is_active=False)

        make_product("iPhone", category=cls.phones)
        make_product("Funda iPhone", category=cls.cases)
        make_product(
            "Antivirus",
            category=cls.software,
            product_type=ProductType.SOFTWARE,
            stock=0,
        )
        make_product("Sin categoría")

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_slug_is_generated(self):
        self.assertEqual(self.phones.slug, "celulares")

    def test_only_two_levels(self):
        deep = self.Category(name="Silicón", parent=self.cases)
        with self.assertRaises(ValidationError):
            deep.full_clean()

    def test_duplicate_sibling_rejected(self):
        dup = self.Category(name="fundas", parent=self.phones)
        with self.assertRaises(ValidationError):
            dup.full_clean()

    def test_public_list_hides_inactive(self):
        response = self.client.get(self.categories_url)
        self.assertEqual(response.status_code, 200)
        names = [c["name"] for c in response.data]
        self.assertNotIn("Oculta", names)
        self.assertIn("Fundas", names)

    def test_customer_cannot_create_category(self):
        self.client.force_authenticate(self.customer)
        response = self.client.post(self.categories_url, {"name": "Hack"})
        self.assertEqual(response.status_code, 403)

    def test_filter_parent_includes_children(self):
        self.client.force_authenticate(self.customer)
        response = self.client.get(
            self.products_url, {"category": "celulares", "page_size": 10}
        )
        names = {p["name"] for p in response.data["results"]}
        self.assertEqual(names, {"iPhone", "Funda iPhone"})

    def test_product_shows_category(self):
        self.client.force_authenticate(self.customer)
        response = self.client.get(f"{self.products_url}antivirus/")
        self.assertEqual(response.data["category"]["slug"], "software")

    def test_deleting_category_keeps_products(self):
        self.software.delete()
        self.assertTrue(Product.objects.filter(name="Antivirus").exists())
