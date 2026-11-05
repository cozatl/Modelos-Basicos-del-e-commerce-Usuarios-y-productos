"""Customer emails. Uses Django's EMAIL_BACKEND (console by default: see settings)."""

from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import send_mail


def _mxn(amount):
    return f"${amount:,.2f} MXN"


def _local(dt):
    tz = ZoneInfo(getattr(settings, "STORE_TIME_ZONE", "America/Mexico_City"))
    return dt.astimezone(tz).strftime("%d/%m/%Y %H:%M")


def order_confirmation_text(order):
    store = getattr(settings, "STORE_NAME", "Virtual Store")
    lines = [
        f"Hola {order.user.get_full_name()},",
        "",
        f"Gracias por tu compra en {store}. Recibimos tu pago.",
        "",
        f"Pedido: {order.order_id}",
        f"Fecha de pago: {_local(order.paid_at)}" if order.paid_at else "",
        "",
        "Productos:",
    ]
    for item in order.items.all():
        lines.append(f"  - {item.product_name} x {item.quantity}: {_mxn(item.subtotal)}")
    lines += [
        "",
        f"Subtotal productos: {_mxn(order.items_subtotal)}",
    ]
    if order.needs_shipping:
        shipping = "Gratis" if order.shipping_cost == 0 else _mxn(order.shipping_cost)
        lines.append(f"Envío ({order.shipping_zone_name}): {shipping}")
    rate = int(order.tax_rate * 100)
    lines += [
        f"Total: {_mxn(order.total)}",
        f"  Incluye IVA {rate}%: {_mxn(order.tax_amount)}",
        f"  Importe sin IVA: {_mxn(order.subtotal_before_tax)}",
    ]
    if order.has_shipping_address:
        lines += [
            "",
            "Enviaremos tu pedido a:",
            *[f"  {x}" for x in order.shipping_address_lines()],
        ]
    if any(not item.is_physical for item in order.items.all()):
        lines += [
            "",
            "Tus productos digitales estarán disponibles en tu cuenta.",
        ]
    frontend = getattr(settings, "FRONTEND_URL", "")
    if frontend:
        lines += [
            "",
            f"Consulta tu pedido en {frontend}/orders/{order.order_id}",
        ]
    lines += ["", f"— {store}"]
    return "\n".join(line for line in lines if line is not None)


def send_order_confirmation(order):
    store = getattr(settings, "STORE_NAME", "Virtual Store")
    send_mail(
        subject=f"{store}: confirmación de tu pedido {order.order_id}",
        message=order_confirmation_text(order),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[order.customer_email],
        fail_silently=False,
    )
