"""
Analytics reads orders; its only table is the staff profile that decides what a
staff member sees in the reports and which commission percentage they earn.
"""

from typing import ClassVar

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class StaffProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="staff_profile",
        limit_choices_to={"is_staff": True},
    )
    locations = models.ManyToManyField(
        "addresses.StockLocation",
        blank=True,
        related_name="staff_profiles",
        help_text='Branches whose reports this person sees (group "Reportes: sucursal").',
    )
    commission_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Percentage of each sale credited to this seller, e.g. 10 = 10 %. "
        "Empty = no commission. Applies to sales assigned from now on.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "staff profile"
        permissions: ClassVar[list] = [
            ("view_global_reports", "Can see the reports of every branch and channel"),
            ("view_location_reports", "Can see the reports of the assigned branches"),
            ("view_own_sales", "Can see the reports of their own sales"),
        ]

    def __str__(self):
        return f"Staff profile of {self.user}"
