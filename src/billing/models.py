from typing import ClassVar

from django.core.validators import MinValueValidator
from django.db import models


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    DECLINED = "declined", "Declined"
    REFUNDED = "refunded", "Refunded"


class Payment(models.Model):
    """
    One payment attempt for an order. An order can have several (declined
    attempts followed by an approved one). The provider is "simulated" until a
    real gateway (Stripe, Mercado Pago, Openpay...) is plugged in;
    see billing.payments.

    Card data never reaches this server: real providers tokenise it
    in the browser.
    """

    order = models.ForeignKey(
        "orders.Order", on_delete=models.PROTECT, related_name="payments"
    )
    provider = models.CharField(max_length=30, default="simulated")
    status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(0)]
    )
    currency = models.CharField(max_length=3, default="MXN")
    # Id of the charge at the provider (used to refund and to match webhooks).
    reference = models.CharField(max_length=100, unique=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    raw_response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    refunded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes: ClassVar[list] = [
            models.Index(
                fields=("order", "status"),
                name="billing_pay_order_i_5e2c1a_idx",
            )
        ]

    def __str__(self):
        return f"{self.reference} · {self.get_status_display()} · ${self.amount}"
