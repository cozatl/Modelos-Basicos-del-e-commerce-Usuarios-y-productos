from django.contrib import admin
from django.contrib.admin.utils import unquote
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm
from django.core.exceptions import PermissionDenied

from .models import User
from .permissions import StaffAccessMixin


class UserCreationForm(AdminUserCreationForm):
    class Meta:
        model = User
        fields = ("email", "full_name")


@admin.register(User)
class UserAdmin(StaffAccessMixin, DjangoUserAdmin):
    add_form = UserCreationForm
    ordering = ("-id",)
    list_display = (
        "email",
        "full_name",
        "is_staff",
        "is_superuser",
        "is_active",
    )
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("email", "full_name")
    readonly_fields = ("last_login", "timestamp")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Datos", {"fields": ("full_name",)}),
        (
            "Rol y permisos",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Fechas", {"fields": ("last_login", "timestamp")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "full_name",
                    "usable_password",
                    "password1",
                    "password2",
                    "is_staff",
                ),
            },
        ),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(is_superuser=False)

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        if not request.user.is_superuser:
            fields += ("is_superuser", "groups", "user_permissions")
        return fields

    def user_change_password(self, request, id, form_url=""):
        # Same rule as the API: staff can't change another staff member's
        # password.
        target = self.get_object(request, unquote(id))
        if (
            target is not None
            and not request.user.is_superuser
            and target.is_staff
            and target.pk != request.user.pk
        ):
            raise PermissionDenied
        return super().user_change_password(request, id, form_url)
