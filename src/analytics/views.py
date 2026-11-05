"""
Report API (JWT). Mounted at /api/v1/analytics/. Every endpoint is GET, accepts the
filters described in analytics.filters and only returns data inside the viewer's
scope (analytics.scope).
"""

from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import reports
from .filters import parse_filters
from .scope import scope_for


class HasReportAccess(BasePermission):
    message = "You do not have permission to see reports."

    def has_permission(self, request, view):
        scope = scope_for(request.user)
        if scope is None:
            return False
        view.scope = scope
        return True


class ReportView(APIView):
    permission_classes = (IsAuthenticated, HasReportAccess)
    scope = None

    def get(self, request):
        filters = parse_filters(request.query_params, self.scope)
        return Response(self.build(filters, request.query_params))

    def build(self, filters, params):  # pragma: no cover - overridden
        raise NotImplementedError


class MetaView(APIView):
    """Scope, branches, channels and sellers the dashboard can offer as filters."""

    permission_classes = (IsAuthenticated, HasReportAccess)
    scope = None

    def get(self, request):
        return Response(reports.meta(self.scope))


class SummaryView(ReportView):
    def build(self, filters, params):
        return reports.summary(self.scope, filters)


class TimeseriesView(ReportView):
    def build(self, filters, params):
        return reports.timeseries(self.scope, filters)


class TopProductsView(ReportView):
    """?order_by=revenue|units&limit=10 (max 50)"""

    def build(self, filters, params):
        limit = params.get("limit") or 10
        try:
            limit = int(limit)
        except ValueError:
            limit = 10
        return reports.top_products(
            self.scope, filters, order_by=params.get("order_by"), limit=limit
        )


class CategoriesView(ReportView):
    """?level=top|leaf"""

    def build(self, filters, params):
        return reports.categories(self.scope, filters, level=params.get("level", "top"))


class BreakdownView(ReportView):
    def build(self, filters, params):
        return reports.breakdown(self.scope, filters)


class OperationsView(ReportView):
    def build(self, filters, params):
        return reports.operations(self.scope, filters)


class CommissionsView(ReportView):
    def build(self, filters, params):
        return reports.commissions(self.scope, filters)
