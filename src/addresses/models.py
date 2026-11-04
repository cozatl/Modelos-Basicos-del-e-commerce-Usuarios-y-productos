import re
from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models, transaction

from .choices import MexicanState

postal_code_validator = RegexValidator(
    r"^\d{5}$", "The postal code must have exactly 5 digits."
)
phone_validator = RegexValidator(
    r"^\d{10}$", "The phone number must have exactly 10 digits."
)


class Address(models.Model):
    """
    A delivery address of a customer (Mexican format). A user may keep several,
    one of them marked as default. Orders copy the fields they need
    (see :meth:`as_snapshot`), so editing or deleting an address never changes
    where a past order was shipped.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="addresses",
    )
    label = models.CharField(
        max_length=50,
        blank=True,
        help_text='Optional name, e.g. "Casa", "Oficina".',
    )
    recipient_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=10, validators=[phone_validator])
    street = models.CharField(max_length=120)
    exterior_number = models.CharField(max_length=20)
    interior_number = models.CharField(max_length=20, blank=True)
    neighborhood = models.CharField("colonia", max_length=120)
    postal_code = models.CharField(max_length=5, validators=[postal_code_validator])
    municipality = models.CharField("municipio / alcaldía", max_length=120)
    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=3, choices=MexicanState.choices)
    references = models.TextField(
        blank=True, help_text="Between streets, facade colour, etc."
    )
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "addresses"
        ordering = ("-is_default", "-updated_at", "-id")
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=("user",),
                condition=models.Q(is_default=True),
                name="one_default_address_per_user",
            ),
        ]

    def __str__(self):
        interior = f" int. {self.interior_number}" if self.interior_number else ""
        return (
            f"{self.street} {self.exterior_number}{interior}, "
            f"{self.neighborhood}, "
            f"CP {self.postal_code}"
        )

    # Fields copied to the order when it is created.
    SNAPSHOT_FIELDS = (
        "recipient_name",
        "phone",
        "street",
        "exterior_number",
        "interior_number",
        "neighborhood",
        "postal_code",
        "municipality",
        "city",
        "state",
        "references",
    )

    def as_snapshot(self):
        return {field: getattr(self, field) for field in self.SNAPSHOT_FIELDS}

    # --- default address handling --------------------------------------------
    def make_default(self):
        """Mark this address as the user's default
        (and unmark the previous one)."""
        with transaction.atomic():
            Address.objects.filter(user_id=self.user_id, is_default=True).exclude(
                pk=self.pk
            ).update(is_default=False)
            if not self.is_default:
                self.is_default = True
                self.save(update_fields=("is_default", "updated_at"))

    def save(self, *args, **kwargs):
        with transaction.atomic():
            # The first address of a user is always the default one.
            if not self.pk and not self.is_default:
                self.is_default = not Address.objects.filter(
                    user_id=self.user_id
                ).exists()
            if self.is_default:
                Address.objects.filter(user_id=self.user_id, is_default=True).exclude(
                    pk=self.pk
                ).update(is_default=False)
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            was_default, user_id = self.is_default, self.user_id
            result = super().delete(*args, **kwargs)
            if was_default:
                # Promote the most recently used one so the user keeps
                # a default.
                successor = (
                    Address.objects.filter(user_id=user_id)
                    .order_by("-updated_at", "-id")
                    .first()
                )
                if successor:
                    successor.make_default()
            return result


# -----------------------------------------------------------------------------
# Shipping by postal-code zones
# -----------------------------------------------------------------------------
_SEPARATORS = re.compile(r"[\s,;]+")


def parse_postal_codes(raw):
    """'01000, 01010\\n01020' -> ['01000', '01010', '01020']
    (unique, sorted, unvalidated)."""
    return sorted({code for code in _SEPARATORS.split(raw or "") if code})


class ShippingZone(models.Model):
    """
    A list of postal codes sharing one flat shipping cost per order.
    Typical setup: "Zone 1" (local, free or cheap), "Zone 2" (medium)
    and "Zone 3"
    (regular rate, also used for postal codes that are not listed anywhere; see
    ShippingSettings.default_zone).
    """

    name = models.CharField(max_length=60, unique=True)
    cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text=("Flat cost per order, VAT included. 0 = free shipping in this zone"),
    )
    postal_codes = models.TextField(
        blank=True,
        help_text="5-digit postal codes, one per line or separated by commas.",
    )
    allows_free_shipping = models.BooleanField(
        default=False,
        help_text=(
            "Free shipping over the threshold applies here when the shipping "
            'settings limit it to "marked zones only".'
        ),
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "name")

    def __str__(self):
        return self.name

    @property
    def postal_code_list(self):
        return parse_postal_codes(self.postal_codes)

    def clean(self):
        super().clean()
        codes = self.postal_code_list
        invalid = [code for code in codes if not re.fullmatch(r"\d{5}", code)]
        if invalid:
            raise ValidationError(
                {"postal_codes": (f"Invalid postal codes: {', '.join(invalid[:10])}")}
            )
        # A postal code can only belong to one active zone, otherwise the cost
        # is ambiguous.
        if self.is_active and codes:
            mine = set(codes)
            others = ShippingZone.objects.filter(is_active=True).exclude(pk=self.pk)
            for other in others:
                repeated = sorted(mine & set(other.postal_code_list))
                if repeated:
                    raise ValidationError(
                        {
                            "postal_codes": (
                                f'Already in zone "{other.name}": '
                                f"{', '.join(repeated[:10])}"
                            )
                        }
                    )

    def save(self, *args, **kwargs):
        # Stored normalised: one code per line, sorted, without duplicates.
        self.postal_codes = "\n".join(self.postal_code_list)
        super().save(*args, **kwargs)


class ShippingSettings(models.Model):
    """Store-wide shipping options. Singleton: always pk=1
    (use :meth:`load`)."""

    default_zone = models.ForeignKey(
        ShippingZone,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=("Zone (and cost) used for postal codes that are not in any zone."),
    )
    free_shipping_threshold = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=(
            "Orders whose products add up to this amount (VAT included) ship for "
            "free. Empty = no free shipping by amount."
        ),
    )
    free_shipping_only_marked_zones = models.BooleanField(
        default=False,
        help_text=(
            "If checked, free shipping by amount only applies in zones marked with "
            '"allows free shipping". If not, it applies everywhere.'
        ),
    )

    class Meta:
        verbose_name = "shipping settings"
        verbose_name_plural = "shipping settings"

    def __str__(self):
        return "Shipping settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.select_related("default_zone").get_or_create(pk=1)
        return obj
