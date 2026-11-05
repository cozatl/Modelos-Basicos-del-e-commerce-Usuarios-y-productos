"""Query-string filters shared by every report endpoint.

    ?start=2026-10-01&end=2026-10-31   local dates (STORE_TIME_ZONE), both included
    &channel=online|pos
    &location=<StockLocation id>
    &seller=<user id>
    &interval=day|week|month           (timeseries; chosen from the range if absent)

Without dates the range is the last 30 days, today included.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from orders.choices import OrderChannel

DEFAULT_DAYS = 30
MAX_DAYS = 731  # two years
MAX_DAILY_DAYS = 186  # a daily chart longer than ~6 months is unreadable
INTERVALS = ("day", "week", "month")


def store_tz():
    return ZoneInfo(getattr(settings, "STORE_TIME_ZONE", "America/Mexico_City"))


def local_today():
    return timezone.now().astimezone(store_tz()).date()


@dataclass(frozen=True)
class ReportFilters:
    start: date
    end: date
    channel: str | None = None
    location_id: int | None = None
    seller_id: int | None = None
    interval: str = "day"

    @property
    def days(self):
        return (self.end - self.start).days + 1

    # Half-open datetime range [start 00:00, end+1 00:00) in the store's time zone
    @property
    def start_dt(self):
        return datetime.combine(self.start, time.min, tzinfo=store_tz())

    @property
    def end_dt(self):
        return datetime.combine(self.end + timedelta(days=1), time.min, tzinfo=store_tz())

    def previous(self):
        """Same number of days right before this range (for comparisons)."""
        length = timedelta(days=self.days)
        return ReportFilters(
            start=self.start - length,
            end=self.start - timedelta(days=1),
            channel=self.channel,
            location_id=self.location_id,
            seller_id=self.seller_id,
            interval=self.interval,
        )

    def as_dict(self):
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "days": self.days,
            "channel": self.channel,
            "location": self.location_id,
            "seller": self.seller_id,
            "interval": self.interval,
        }

    def apply(self, orders):
        """Channel / branch / seller filters on an Order queryset (no dates)."""
        if self.channel:
            orders = orders.filter(channel=self.channel)
        if self.location_id:
            orders = orders.filter(location_id=self.location_id)
        if self.seller_id:
            orders = orders.filter(sold_by_id=self.seller_id)
        return orders


def default_interval(days):
    if days <= 62:
        return "day"
    if days <= 366:
        return "week"
    return "month"


def _date(params, name, errors):
    raw = params.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        errors[name] = "Use the format YYYY-MM-DD."
        return None


def _int(params, name, errors):
    raw = params.get(name)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except ValueError:
        errors[name] = "Must be a number."
        return None


def parse_filters(params, scope):
    errors = {}
    start = _date(params, "start", errors)
    end = _date(params, "end", errors)
    location_id = _int(params, "location", errors)
    seller_id = _int(params, "seller", errors)
    channel = params.get("channel") or None
    if channel and channel not in OrderChannel.values:
        errors["channel"] = f"Choose one of: {', '.join(OrderChannel.values)}."
    interval = params.get("interval") or None
    if interval and interval not in INTERVALS:
        errors["interval"] = f"Choose one of: {', '.join(INTERVALS)}."
    if errors:
        raise ValidationError(errors)

    end = end or local_today()
    start = start or end - timedelta(days=DEFAULT_DAYS - 1)
    if start > end:
        raise ValidationError({"start": "The start date must be before the end date."})
    days = (end - start).days + 1
    if days > MAX_DAYS:
        raise ValidationError(
            {"start": f"The range cannot be longer than {MAX_DAYS} days."}
        )
    interval = interval or default_interval(days)
    if interval == "day" and days > MAX_DAILY_DAYS:
        raise ValidationError(
            {"interval": f"Use week or month for ranges over {MAX_DAILY_DAYS} days."}
        )

    if location_id is not None and not scope.can_see_location(location_id):
        raise PermissionDenied("You cannot see the reports of that branch.")
    if seller_id is not None and not scope.can_filter_by_seller(seller_id):
        raise PermissionDenied("You can only see your own sales.")

    return ReportFilters(
        start=start,
        end=end,
        channel=channel,
        location_id=location_id,
        seller_id=seller_id,
        interval=interval,
    )
