from django.db import models


class ProductType(models.TextChoices):
    """What the product is."""

    PHYSICAL = "physical", "Físico"
    SOFTWARE = "software", "Software"
    EBOOK = "ebook", "Libro PDF"
    VIDEO = "video", "Video"


class AccessType(models.TextChoices):
    """How the customer gets the product."""

    PURCHASE = "purchase", "Compra"
    SUBSCRIPTION = "subscription", "Suscripción"
    FREE = "free", "Gratis"
