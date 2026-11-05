from django.contrib import admin

from .models import StaffProfile


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    """
    One row per staff member who sells or sees branch reports:
    - Locations: branches whose reports they see (needs group "Reportes: sucursal").
    - Commission %: credited on each sale assigned to them from now on.
    """

    list_display = ("user", "commission_percent", "location_names", "updated_at")
    list_filter = ("locations",)
    search_fields = ("user__email", "user__full_name")
    autocomplete_fields = ("user",)
    filter_horizontal = ("locations",)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("user")
            .prefetch_related("locations")
        )

    @admin.display(description="Branches")
    def location_names(self, obj):
        return ", ".join(loc.name for loc in obj.locations.all()) or "—"
