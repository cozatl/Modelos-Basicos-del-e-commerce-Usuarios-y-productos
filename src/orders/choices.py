from django.db import models


class OrderStatus(models.TextChoices):
    """
    pending_payment -> paid -> shipped -> delivered
    pending_payment -> expired      (the reservation ran out, stock goes back)
    pending_payment -> cancelled    (the customer or staff cancels
                                     before paying)
    paid/shipped/delivered -> refunded  (staff approves a cancellation/return)
    """

    PENDING_PAYMENT = "pending_payment", "Pending payment"
    PAID = "paid", "Paid"
    SHIPPED = "shipped", "Shipped"
    DELIVERED = "delivered", "Delivered"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"
    REFUNDED = "refunded", "Refunded"


# Orders that count as a sale (money received and not given back).
SOLD_STATUSES = (OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED)
