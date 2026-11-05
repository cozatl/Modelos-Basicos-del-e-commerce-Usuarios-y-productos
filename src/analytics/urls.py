from django.urls import re_path

from analytics import views

app_name = "analytics"

# Mounted at /api/v1/analytics/ (trailing slash optional, like the cart endpoints)
urlpatterns = [
    re_path(r"^meta/?$", views.MetaView.as_view(), name="meta"),
    re_path(r"^summary/?$", views.SummaryView.as_view(), name="summary"),
    re_path(r"^timeseries/?$", views.TimeseriesView.as_view(), name="timeseries"),
    re_path(r"^products/?$", views.TopProductsView.as_view(), name="products"),
    re_path(r"^categories/?$", views.CategoriesView.as_view(), name="categories"),
    re_path(r"^breakdown/?$", views.BreakdownView.as_view(), name="breakdown"),
    re_path(r"^operations/?$", views.OperationsView.as_view(), name="operations"),
    re_path(r"^commissions/?$", views.CommissionsView.as_view(), name="commissions"),
]
