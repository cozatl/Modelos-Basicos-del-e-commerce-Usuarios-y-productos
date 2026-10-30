from django.conf import settings
from django.db import models

from addresses.models import Address


class BillingProfile(models.Model):
    # user = models.ForeignKey("auth.User", on_delete=models.CASCADE)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    address = models.ForeignKey(Address, on_delete=models.CASCADE)

    def __str__(self):
        return str(self.user)
