import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Avg, Sum
from django.utils import timezone

from products.models import Product


class OrderQuerySet(models.query.QuerySet):
    def recent(self):
        return self.order_by("-updated", "-timestamp")

    def get_sales_breakdown(self):
        recent = self.recent().not_refunded()
        recent_data = recent.totals_data()
        recent_cart_data = recent.cart_data()
        shipped = recent.by_status(status="shipped")
        shipped_data = shipped.totals_data()
        paid = recent.by_status(status="paid")
        paid_data = paid.totals_data()
        data = {
            "recent": recent,
            "recent_data": recent_data,
            "recent_cart_data": recent_cart_data,
            "shipped": shipped,
            "shipped_data": shipped_data,
            "paid_data": paid_data,
        }
        return data

    def by_status(self, status="shipped"):
        return self.filter(status=status)

    def not_refunded(self):
        return self.exclude(status="refunded")

    def not_created(self):
        return self.exclude(status="created")

    def by_range(self, start_date, end_date=None):
        if end_date is None:
            return self.filter(created_at__gte=start_date)
        return self.filter(created_at__gte=start_date).filter(
            created_at__lte=end_date
        )

    def totals_data(self):
        return self.aggregate(Sum("total"), Avg("total"))

    def by_weeks_range(self, weeks_ago=7, number_of_weeks=2):
        number_of_weeks = min(number_of_weeks, weeks_ago)
        days_ago_start = weeks_ago * 7
        days_ago_end = days_ago_start - (number_of_weeks * 7)
        start_date = timezone.now() - timedelta(days=days_ago_start)
        end_date = timezone.now() - timedelta(days=days_ago_end)
        return self.by_range(start_date, end_date=end_date)


class Cart(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    products = models.ManyToManyField(Product, blank=True)

    def __str__(self):
        return f"Cart #{self.id}"


class Order(models.Model):
    cart = models.OneToOneField(Cart, on_delete=models.CASCADE)
    order_id = models.CharField(max_length=120, blank=True, unique=True)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(auto_now_add=True)
    # created_at = models.DateTimeField()

    def save(self, *args, **kwargs):
        if not self.order_id:
            self.order_id = str(uuid.uuid4()).replace("-", "").upper()[:12]
        super().save(*args, **kwargs)

    def __str__(self):
        return self.order_id

    objects = OrderQuerySet.as_manager()
