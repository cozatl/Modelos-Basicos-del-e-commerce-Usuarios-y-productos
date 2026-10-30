from django.urls import path

from analytics import views

# Define namespace which is required for the template
app_name = "analytics"

urlpatterns = [
    # Weekly sales chart data (staff only). Replaced by a JWT API in M5.
    path("data/", views.SalesAjaxView.as_view(), name="sales-data"),
]
