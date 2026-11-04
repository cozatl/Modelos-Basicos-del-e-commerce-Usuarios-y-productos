"""
Payment providers.

Only the simulated provider exists for now. A real one (Stripe, Mercado Pago,
Openpay, Conekta...) must implement the same two methods; the checkout code
only calls ``get_provider().charge(...)`` and ``.refund(...)``. With a real
gateway the charge is usually confirmed by a webhook instead of synchronously:
that lands in M-final.
"""

import uuid

from django.conf import settings
from django.utils import timezone

from .models import Payment, PaymentStatus


class PaymentsDisabled(Exception):
    """No payment provider is configured."""


class SimulatedProvider:
    """Approves or declines on request. Never use it with real money."""

    name = "simulated"

    def charge(self, order, outcome="approved"):
        approved = outcome == "approved"
        return Payment.objects.create(
            order=order,
            provider=self.name,
            amount=order.total,
            currency=order.currency,
            reference=f"SIM-{uuid.uuid4().hex[:20].upper()}",
            status=(PaymentStatus.APPROVED if approved else PaymentStatus.DECLINED),
            failure_reason="" if approved else "Card declined (simulated).",
            raw_response={"simulated": True, "outcome": outcome},
        )

    def refund(self, payment):
        payment.status = PaymentStatus.REFUNDED
        payment.refunded_at = timezone.now()
        payment.save(update_fields=("status", "refunded_at", "updated_at"))
        return payment


def get_provider():
    if getattr(settings, "PAYMENTS_SIMULATED", False):
        return SimulatedProvider()
    raise PaymentsDisabled("No payment provider is configured.")
