from datetime import timedelta

from django.http import JsonResponse
from django.utils import timezone
from django.views.generic import View

from orders.models import Order


class SalesAjaxView(View):
    def get(self, request, *args, **kwargs):
        data = {}
        if request.user.is_staff:
            qs = Order.objects.by_weeks_range(weeks_ago=5, number_of_weeks=5)
            if request.GET.get("type") == "week":
                days = 7
                start_date = timezone.localdate() - timedelta(days=days - 1)
                labels, sales_items = [], []
                for x in range(days):
                    day = start_date + timedelta(days=x)
                    labels.append(day.strftime("%a"))
                    day_total = (
                        qs.filter(created_at__date=day).totals_data()[
                            "total__sum"
                        ]
                        or 0
                    )
                    sales_items.append(float(day_total))
                data["labels"] = labels
                data["data"] = sales_items
        return JsonResponse(data)
