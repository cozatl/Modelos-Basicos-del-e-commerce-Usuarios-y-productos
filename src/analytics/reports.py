"""
Report builders. Every function takes the viewer's ReportScope and the parsed
ReportFilters and returns plain JSON-ready data (money as "1234.56" strings, like
the rest of the API).

What counts as a sale: orders paid, shipped or delivered (Order.objects.sold()),
dated by ``paid_at`` in the store's time zone. Refunded orders are reported apart
(dated by when they were refunded) and never add to sales or commissions.
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.auth import get_user_model
from django.db.models import Count, F, Max, Q, Sum
from django.db.models.functions import Coalesce, TruncDay, TruncMonth, TruncWeek

from addresses.choices import LocationKind
from orders.choices import SOLD_STATUSES, OrderChannel, OrderStatus
from orders.models import Order, OrderItem
from products.choices import ProductType
from products.models import Category

from .commissions import BASE_LABELS, commission_base_mode
from .filters import DEFAULT_DAYS, local_today, store_tz

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
TRUNC = {"day": TruncDay, "week": TruncWeek, "month": TruncMonth}


def money(value):
    return str(Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP))


def change_percent(current, previous):
    """Growth vs the previous period, one decimal; None when there is no base."""
    current, previous = Decimal(current or 0), Decimal(previous or 0)
    if previous == 0:
        return None
    return float(((current - previous) / previous * 100).quantize(Decimal("0.1")))


# ---------------------------------------------------------------------------------
# Querysets
# ---------------------------------------------------------------------------------
def scoped_orders(scope, filters):
    """Orders the viewer may see, with channel/branch/seller filters (no dates)."""
    return filters.apply(Order.objects.filter(scope.q()))


def sales(scope, filters):
    return (
        scoped_orders(scope, filters)
        .sold()
        .filter(paid_at__gte=filters.start_dt, paid_at__lt=filters.end_dt)
    )


def refunds(scope, filters):
    return scoped_orders(scope, filters).filter(
        status=OrderStatus.REFUNDED,
        cancelled_at__gte=filters.start_dt,
        cancelled_at__lt=filters.end_dt,
    )


def items_of(orders):
    return OrderItem.objects.filter(order__in=orders)


# ---------------------------------------------------------------------------------
# Summary (KPI cards)
# ---------------------------------------------------------------------------------
def _totals(scope, filters):
    qs = sales(scope, filters)
    agg = qs.aggregate(
        orders=Count("id"),
        amount=Sum("total"),
        items_sum=Sum("items_subtotal"),
        shipping=Sum("shipping_cost"),
        tax=Sum("tax_amount"),
        commission=Sum("commission_amount"),
    )
    units = items_of(qs).aggregate(units=Sum("quantity"))["units"] or 0
    refunded = refunds(scope, filters).aggregate(count=Count("id"), amount=Sum("total"))
    total = agg["amount"] or ZERO
    orders = agg["orders"] or 0
    return {
        "orders": orders,
        "units": units,
        "sales_total": total,
        "net_sales": total - (agg["tax"] or ZERO),  # without VAT
        "items_total": agg["items_sum"] or ZERO,
        "shipping_total": agg["shipping"] or ZERO,
        "tax_total": agg["tax"] or ZERO,
        "average_ticket": (total / orders) if orders else ZERO,
        "commission_total": agg["commission"] or ZERO,
        "refunds_count": refunded["count"] or 0,
        "refunds_total": refunded["amount"] or ZERO,
    }


MONEY_KEYS = (
    "sales_total",
    "net_sales",
    "items_total",
    "shipping_total",
    "tax_total",
    "average_ticket",
    "commission_total",
    "refunds_total",
)


def _serialize_totals(totals):
    return {k: money(v) if k in MONEY_KEYS else v for k, v in totals.items()}


def summary(scope, filters):
    current = _totals(scope, filters)
    previous_filters = filters.previous()
    previous = _totals(scope, previous_filters)
    return {
        "range": filters.as_dict(),
        "previous_range": {
            "start": previous_filters.start.isoformat(),
            "end": previous_filters.end.isoformat(),
        },
        "current": _serialize_totals(current),
        "previous": _serialize_totals(previous),
        "change": {
            key: change_percent(current[key], previous[key])
            for key in ("sales_total", "net_sales", "orders", "units", "average_ticket")
        },
    }


# ---------------------------------------------------------------------------------
# Time series (chart)
# ---------------------------------------------------------------------------------
def _bucket_starts(filters):
    if filters.interval == "week":
        current = filters.start - timedelta(days=filters.start.weekday())
    elif filters.interval == "month":
        current = filters.start.replace(day=1)
    else:
        current = filters.start
    while current <= filters.end:
        yield current
        if filters.interval == "day":
            current += timedelta(days=1)
        elif filters.interval == "week":
            current += timedelta(days=7)
        else:
            current = date(current.year + current.month // 12, current.month % 12 + 1, 1)


def timeseries(scope, filters):
    tz = store_tz()
    trunc = TRUNC[filters.interval]("paid_at", tzinfo=tz)
    rows = (
        sales(scope, filters)
        .annotate(bucket=trunc)
        .values("bucket")
        .annotate(
            orders=Count("id"),
            amount=Sum("total"),
            online=Sum("total", filter=Q(channel=OrderChannel.ONLINE)),
            pos=Sum("total", filter=Q(channel=OrderChannel.POS)),
        )
    )
    by_bucket = {row["bucket"].astimezone(tz).date(): row for row in rows}
    points = []
    for start in _bucket_starts(filters):
        row = by_bucket.get(start, {})
        points.append(
            {
                "period": start.isoformat(),
                "orders": row.get("orders", 0),
                "total": money(row.get("amount")),
                "online": money(row.get("online")),
                "pos": money(row.get("pos")),
            }
        )
    return {"range": filters.as_dict(), "interval": filters.interval, "points": points}


# ---------------------------------------------------------------------------------
# Products, categories and product types
# ---------------------------------------------------------------------------------
PRODUCT_ORDERING = {"units": "units", "revenue": "revenue"}


def top_products(scope, filters, order_by="revenue", limit=10):
    key = PRODUCT_ORDERING.get(order_by, "revenue")
    limit = max(1, min(int(limit or 10), 50))
    rows = (
        items_of(sales(scope, filters))
        .values("product_id", "sku")
        .annotate(
            name=Max("product_name"),
            type_code=Max("product_type"),
            units=Sum("quantity"),
            revenue=Sum("subtotal"),
            orders=Count("order", distinct=True),
        )
        .order_by(f"-{key}", "sku")[:limit]
    )
    return {
        "range": filters.as_dict(),
        "order_by": key,
        "results": [
            {
                "product_id": row["product_id"],
                "sku": row["sku"],
                "name": row["name"],
                "product_type": row["type_code"],
                "units": row["units"],
                "revenue": money(row["revenue"]),
                "orders": row["orders"],
            }
            for row in rows
        ],
    }


def categories(scope, filters, level="top"):
    """Sales by category. level=top folds subcategories into their parent."""
    items = items_of(sales(scope, filters))
    if level == "top":
        category = Coalesce("product__category__parent_id", "product__category_id")
    else:
        level = "leaf"
        category = F("product__category_id")
    rows = list(
        items.annotate(category=category)
        .values("category")
        .annotate(units=Sum("quantity"), revenue=Sum("subtotal"))
        .order_by("-revenue")
    )
    names = Category.objects.select_related("parent").in_bulk(
        [r["category"] for r in rows if r["category"]]
    )
    by_type = (
        items.values("product_type")
        .annotate(units=Sum("quantity"), revenue=Sum("subtotal"))
        .order_by("-revenue")
    )
    type_labels = dict(ProductType.choices)
    return {
        "range": filters.as_dict(),
        "level": level,
        "categories": [
            {
                "category_id": r["category"],
                "name": str(names[r["category"]])
                if r["category"] in names
                else "Sin categoría",
                "units": r["units"],
                "revenue": money(r["revenue"]),
            }
            for r in rows
        ],
        "product_types": [
            {
                "product_type": r["product_type"],
                "label": type_labels.get(r["product_type"], r["product_type"]),
                "units": r["units"],
                "revenue": money(r["revenue"]),
            }
            for r in by_type
        ],
    }


# ---------------------------------------------------------------------------------
# Channel / branch / region
# ---------------------------------------------------------------------------------
def breakdown(scope, filters):
    qs = sales(scope, filters)
    channel_labels = dict(OrderChannel.choices)
    by_channel = qs.values("channel").annotate(orders=Count("id"), amount=Sum("total"))
    by_location = qs.values(
        "location_id", "location__name", "location__region", "location__kind"
    ).annotate(orders=Count("id"), amount=Sum("total"))
    by_region = qs.values("location__region").annotate(
        orders=Count("id"), amount=Sum("total")
    )
    kind_labels = dict(LocationKind.choices)

    def sort(rows):
        return sorted(rows, key=lambda r: Decimal(r["total"]), reverse=True)

    return {
        "range": filters.as_dict(),
        "channels": sort(
            {
                "channel": r["channel"],
                "label": channel_labels.get(r["channel"], r["channel"]),
                "orders": r["orders"],
                "total": money(r["amount"]),
            }
            for r in by_channel
        ),
        "locations": sort(
            {
                "location_id": r["location_id"],
                "name": r["location__name"] or "Sin sucursal",
                "region": r["location__region"] or "",
                "kind": r["location__kind"],
                "kind_label": kind_labels.get(r["location__kind"], ""),
                "orders": r["orders"],
                "total": money(r["amount"]),
            }
            for r in by_location
        ),
        "regions": sort(
            {
                "region": r["location__region"] or "Sin región",
                "orders": r["orders"],
                "total": money(r["amount"]),
            }
            for r in by_region
        ),
    }


# ---------------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------------
def operations(scope, filters):
    """
    ``statuses``: orders CREATED in the range, by their current status (how many
    were paid, expired, cancelled...). ``backlog``: work waiting right now,
    regardless of dates.
    """
    base = scoped_orders(scope, filters)
    created = base.filter(created_at__gte=filters.start_dt, created_at__lt=filters.end_dt)
    counts = {
        row["status"]: row
        for row in created.values("status").annotate(
            count=Count("id"), amount=Sum("total")
        )
    }
    statuses = [
        {
            "status": value,
            "label": label,
            "count": counts.get(value, {}).get("count", 0),
            "total": money(counts.get(value, {}).get("amount")),
        }
        for value, label in OrderStatus.choices
    ]
    created_count = sum(s["count"] for s in statuses)
    reached_payment = sum(
        s["count"]
        for s in statuses
        if s["status"] in (*SOLD_STATUSES, OrderStatus.REFUNDED)
    )
    return {
        "range": filters.as_dict(),
        "created": created_count,
        "paid_rate": round(reached_payment / created_count * 100, 1)
        if created_count
        else None,
        "statuses": statuses,
        "backlog": {
            "awaiting_payment": base.pending_payment().count(),
            "to_ship": base.filter(status=OrderStatus.PAID, needs_shipping=True).count(),
            "cancellation_requests": base.filter(
                cancel_requested_at__isnull=False, status__in=SOLD_STATUSES
            ).count(),
        },
    }


# ---------------------------------------------------------------------------------
# Commissions
# ---------------------------------------------------------------------------------
def commissions(scope, filters):
    rows = (
        sales(scope, filters)
        .filter(sold_by__isnull=False)
        .values("sold_by_id", "sold_by__email", "sold_by__full_name")
        .annotate(
            orders=Count("id"),
            amount=Sum("total"),
            commission=Sum("commission_amount"),
        )
        .order_by("-commission", "sold_by__email")
    )
    rows = list(rows)
    User = get_user_model()
    current = dict(
        User.objects.filter(pk__in=[r["sold_by_id"] for r in rows]).values_list(
            "pk", "staff_profile__commission_percent"
        )
    )
    results = [
        {
            "seller_id": r["sold_by_id"],
            "email": r["sold_by__email"],
            "name": r["sold_by__full_name"] or r["sold_by__email"],
            "orders": r["orders"],
            "sales_total": money(r["amount"]),
            "commission": money(r["commission"]),
            "current_percent": str(current[r["sold_by_id"]])
            if current.get(r["sold_by_id"]) is not None
            else None,
        }
        for r in rows
    ]
    return {
        "range": filters.as_dict(),
        "base": commission_base_mode(),
        "base_label": BASE_LABELS[commission_base_mode()],
        "results": results,
        "totals": {
            "orders": sum(r["orders"] for r in rows),
            "sales_total": money(sum((r["amount"] or ZERO for r in rows), ZERO)),
            "commission": money(sum((r["commission"] or ZERO for r in rows), ZERO)),
        },
    }


# ---------------------------------------------------------------------------------
# Meta: what the dashboard can offer this viewer
# ---------------------------------------------------------------------------------
def meta(scope):
    User = get_user_model()
    if scope.all:
        sellers = User.objects.filter(is_staff=True, is_active=True)
    elif scope.location_ids:
        sellers = User.objects.filter(
            Q(staff_profile__locations__in=scope.location_ids)
            | Q(sales__location_id__in=scope.location_ids)
            | Q(pk=scope.user.pk),
            is_staff=True,
        )
    else:
        sellers = User.objects.filter(pk=scope.user.pk)
    sellers = sellers.distinct().order_by("full_name", "email")
    today = local_today()
    return {
        "scope": {
            "level": scope.level,
            "all": scope.all,
            "location_ids": list(scope.location_ids),
            "own": scope.own,
        },
        "locations": [
            {
                "id": loc.pk,
                "name": loc.name,
                "region": loc.region,
                "kind": loc.kind,
                "is_active": loc.is_active,
            }
            for loc in scope.visible_locations()
        ],
        "channels": [{"value": v, "label": label} for v, label in OrderChannel.choices],
        "sellers": [
            {"id": u.pk, "name": u.full_name or u.email, "email": u.email}
            for u in sellers
        ],
        "commission_base": commission_base_mode(),
        "commission_base_label": BASE_LABELS[commission_base_mode()],
        "time_zone": str(store_tz()),
        "defaults": {
            "start": (today - timedelta(days=DEFAULT_DAYS - 1)).isoformat(),
            "end": today.isoformat(),
        },
    }
