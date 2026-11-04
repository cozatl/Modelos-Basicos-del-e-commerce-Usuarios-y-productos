from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Address, ShippingSettings, ShippingZone
from .shipping import ShippingNotAvailable, quote_shipping

User = get_user_model()

ADDRESS = {
    "label": "Casa",
    "recipient_name": "Jesus Cozatl",
    "phone": "55 1234 5678",
    "street": "Av. Juárez",
    "exterior_number": "10",
    "interior_number": "3B",
    "neighborhood": "Centro",
    "postal_code": " 01000 ",
    "municipality": "Cuauhtémoc",
    "city": "Ciudad de México",
    "state": "CMX",
    "references": "Entre calle A y B",
}


def make_address(user, **overrides):
    data = {
        **ADDRESS,
        "phone": "5512345678",
        "postal_code": "01000",
        **overrides,
    }
    return Address.objects.create(user=user, **data)


def configure_shipping(threshold=None, only_marked=False):
    """Zona 1 (01000, free), Zona 2 (02000, $50, marked), Zona 3 ($150, default)."""
    ShippingSettings.objects.all().delete()
    ShippingZone.objects.all().delete()
    z1 = ShippingZone.objects.create(name="Zona 1", cost=Decimal(0), postal_codes="01000")
    z2 = ShippingZone.objects.create(
        name="Zona 2",
        cost=Decimal(50),
        postal_codes="02000, 02010",
        allows_free_shipping=True,
    )
    z3 = ShippingZone.objects.create(name="Zona 3", cost=Decimal(150))
    ShippingSettings.objects.create(
        default_zone=z3,
        free_shipping_threshold=threshold,
        free_shipping_only_marked_zones=only_marked,
    )
    return z1, z2, z3


class AddressApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="a@example.com", password="x-pass-12345"
        )
        self.other = User.objects.create_user(
            email="b@example.com", password="x-pass-12345"
        )
        self.client.force_authenticate(self.user)
        self.list_url = reverse("address-list")

    def detail_url(self, pk):
        return reverse("address-detail", kwargs={"pk": pk})

    def test_requires_authentication(self):
        self.client.force_authenticate(None)
        self.assertIn(
            self.client.get(self.list_url).status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_create_normalises_digits_and_first_one_is_default(self):
        response = self.client.post(
            self.list_url, {**ADDRESS, "user": self.other.pk}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["phone"], "5512345678")
        self.assertEqual(response.data["postal_code"], "01000")
        self.assertEqual(response.data["state_display"], "Ciudad de México")
        self.assertTrue(response.data["is_default"])
        self.assertEqual(Address.objects.get().user, self.user)  # never from payload

    def test_invalid_postal_code_and_phone(self):
        response = self.client.post(
            self.list_url,
            {**ADDRESS, "postal_code": "123", "phone": "555"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("postal_code", response.data)
        self.assertIn("phone", response.data)

    def test_only_one_default(self):
        first = make_address(self.user)
        second = make_address(self.user, label="Oficina", is_default=True)
        first.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)

        response = self.client.post(
            reverse("address-set-default", kwargs={"pk": first.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_default"])
        second.refresh_from_db()
        self.assertFalse(second.is_default)

    def test_deleting_the_default_promotes_another(self):
        first = make_address(self.user)
        second = make_address(self.user, label="Oficina")
        self.assertTrue(first.is_default)
        response = self.client.delete(self.detail_url(first.pk))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        second.refresh_from_db()
        self.assertTrue(second.is_default)

    def test_other_users_addresses_are_invisible(self):
        theirs = make_address(self.other)
        self.assertEqual(self.client.get(self.list_url).data, [])
        self.assertEqual(
            self.client.get(self.detail_url(theirs.pk)).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.patch(
                self.detail_url(theirs.pk), {"label": "x"}, format="json"
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )


class ShippingQuoteTests(TestCase):
    def test_listed_postal_code_uses_its_zone(self):
        configure_shipping()
        quote = quote_shipping("02010", Decimal(100))
        self.assertEqual(quote.zone_name, "Zona 2")
        self.assertEqual(quote.cost, Decimal(50))
        self.assertFalse(quote.used_default_zone)

    def test_zone_with_cost_zero_is_free(self):
        configure_shipping()
        self.assertEqual(quote_shipping("01000", Decimal(1)).cost, Decimal(0))

    def test_unlisted_postal_code_uses_default_zone(self):
        configure_shipping()
        quote = quote_shipping("99999", Decimal(100))
        self.assertEqual(quote.zone_name, "Zona 3")
        self.assertEqual(quote.cost, Decimal(150))
        self.assertTrue(quote.used_default_zone)

    def test_without_default_zone_unlisted_postal_codes_are_rejected(self):
        configure_shipping()
        ShippingSettings.objects.update(default_zone=None)
        with self.assertRaises(ShippingNotAvailable):
            quote_shipping("99999", Decimal(100))

    def test_global_free_shipping_threshold(self):
        configure_shipping(threshold=Decimal(1000))
        below = quote_shipping("99999", Decimal("999.99"))
        above = quote_shipping("99999", Decimal(1000))
        self.assertEqual(below.cost, Decimal(150))
        self.assertEqual(above.cost, Decimal(0))
        self.assertTrue(above.free_by_threshold)

    def test_threshold_only_in_marked_zones(self):
        configure_shipping(threshold=Decimal(1000), only_marked=True)
        self.assertEqual(
            quote_shipping("02000", Decimal(1500)).cost, Decimal(0)
        )  # marked
        self.assertEqual(quote_shipping("99999", Decimal(1500)).cost, Decimal(150))

    def test_postal_codes_are_normalised_and_cannot_repeat_across_zones(self):
        _, z2, _ = configure_shipping()
        self.assertEqual(z2.postal_codes, "02000\n02010")
        clash = ShippingZone(name="Zona X", cost=Decimal(10), postal_codes="02010;03000")
        with self.assertRaises(ValidationError):
            clash.full_clean()
        bad = ShippingZone(name="Zona Y", cost=Decimal(10), postal_codes="1234")
        with self.assertRaises(ValidationError):
            bad.full_clean()

    def test_seed_migration_created_three_zones(self):
        # Data from migration 0003 (before configure_shipping replaces it)
        self.assertEqual(
            list(ShippingZone.objects.values_list("name", flat=True)),
            ["Zona 1", "Zona 2", "Zona 3"],
        )
        self.assertEqual(ShippingSettings.load().default_zone.name, "Zona 3")
