"""
Celery tasks. The schedule lives in
settings.CELERY_BEAT_SCHEDULE, so a `celery beat` process must run next to the
worker (see docs/M4.md).
"""

from celery import shared_task


@shared_task(name="orders.release_expired_orders")
def release_expired_orders_task():
    from .services import release_expired_orders

    return release_expired_orders()


@shared_task(
    name="orders.send_order_confirmation",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def send_order_confirmation_task(order_pk):
    from .emails import send_order_confirmation
    from .models import Order

    order = (
        Order.objects.select_related("user").prefetch_related("items").get(pk=order_pk)
    )
    send_order_confirmation(order)
