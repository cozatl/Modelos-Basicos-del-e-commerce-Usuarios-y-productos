import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.text import slugify

from .choices import AccessType, ProductType
from .rules import product_rule_errors


class Category(models.Model):
    """Navigation group (e.g. "Celulares", "Antivirus"). Up to two levels."""

    name = models.CharField(max_length=80)
    slug = models.SlugField(unique=True, blank=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,  # can't delete a category with children
        null=True,
        blank=True,
        related_name="children",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)
        verbose_name_plural = "categories"

    def __str__(self):
        if self.parent:
            return f"{self.parent.name} / {self.name}"
        return self.name

    def clean(self):
        super().clean()
        # Same name twice under the same parent (or twice at top level)
        siblings = Category.objects.filter(
            parent_id=self.parent_id, name__iexact=self.name
        ).exclude(pk=self.pk)
        if siblings.exists():
            raise ValidationError(
                {"name": "This category already exists here."}
            )
        if self.parent_id is None:
            return
        if self.pk and self.parent_id == self.pk:
            raise ValidationError(
                {"parent": "A category can't be its own parent."}
            )
        # Two levels only: the parent must be a top-level category
        if self.parent.parent_id is not None:
            raise ValidationError(
                {"parent": "Subcategories can't have their own subcategories."}
            )
        if self.pk and self.children.exists():
            raise ValidationError(
                {"parent": "A category with subcategories can't become one."}
            )

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)
            if (
                self.parent_id
                and Category.objects.filter(slug=base)
                .exclude(pk=self.pk)
                .exists()
            ):
                base = f"{self.parent.slug}-{base}"
            slug, counter = base, 1
            while (
                Category.objects.filter(slug=slug).exclude(pk=self.pk).exists()
            ):
                slug = f"{base}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)


class Product(models.Model):
    # Basic product information
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True, blank=True)
    description = models.TextField()

    # Navigation (optional): a product without category is still listed
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
    )

    # What the product is and how it's obtained
    product_type = models.CharField(
        max_length=20,
        choices=ProductType.choices,
        default=ProductType.PHYSICAL,
    )
    access_type = models.CharField(
        max_length=20,
        choices=AccessType.choices,
        default=AccessType.PURCHASE,
    )

    # Pricing (empty for subscription/free products)
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    discount_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )

    # Kept inside the class: migration 0001 references
    # products.models.Product.generate_sku
    def generate_sku():
        return str(uuid.uuid4())

    # Additional product details (stock and weight: physical only)
    sku = models.CharField(max_length=50, unique=True, default=generate_sku)
    stock = models.PositiveIntegerField(default=0)
    weight = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        help_text="Weight in kg (physical products only)",
        null=True,
        blank=True,
    )

    # Multimedia
    image = models.ImageField(upload_to="products/", null=True, blank=True)

    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(
        default=False,
        help_text="Shown in the public preview of the home page.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")  # id breaks ties (bulk inserts)
        indexes = (
            models.Index(fields=("is_active", "-created_at")),
            models.Index(fields=("is_active", "is_featured", "-created_at")),
        )
        constraints = (
            models.CheckConstraint(
                condition=models.Q(price__gte=0),
                name="price_non_negative",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(discount_price__lt=models.F("price"))
                    | models.Q(discount_price__isnull=True)
                ),
                name="discount_price_less_than_price",
                violation_error_message=(
                    "The discount price must be less than the regular price."
                ),
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(price__isnull=False)
                    | ~models.Q(access_type=AccessType.PURCHASE)
                ),
                name="purchase_requires_price",
                violation_error_message=(
                    "Price is required for products sold by purchase."
                ),
            ),
            models.CheckConstraint(
                condition=~models.Q(
                    product_type=ProductType.PHYSICAL,
                    access_type=AccessType.SUBSCRIPTION,
                ),
                name="physical_not_subscription",
                violation_error_message=(
                    "A physical product can't be sold by subscription."
                ),
            ),
        )

    def __str__(self):
        if self.price is not None:
            return f"{self.name} - ${self.price}"
        return f"{self.name} - {self.get_access_type_display()}"

    @property
    def is_physical(self):
        """Physical products need shipping, stock and weight."""
        return self.product_type == ProductType.PHYSICAL

    @property
    def is_available(self):
        """Digital products never run out; physical ones need stock."""
        return self.is_active and (not self.is_physical or self.stock > 0)

    def clean(self):
        super().clean()
        errors = product_rule_errors(
            product_type=self.product_type,
            access_type=self.access_type,
            price=self.price,
            discount_price=self.discount_price,
            stock=self.stock,
            weight=self.weight,
        )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)
            slug = base_slug
            counter = 1
            while (
                Product.objects.filter(slug=slug).exclude(pk=self.pk).exists()
            ):
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)
