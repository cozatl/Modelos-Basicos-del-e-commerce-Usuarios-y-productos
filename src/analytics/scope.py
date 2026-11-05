"""
What part of the sales a staff member may see. Permissions come from groups
(see orders/management/commands/setup_order_groups.py):

- analytics.view_global_reports   -> every channel and branch (superusers too).
- analytics.view_location_reports -> orders of the branches assigned in their
                                     StaffProfile.
- analytics.view_own_sales        -> orders credited to them (sold_by).

Several permissions add up (branches OR own sales). No permission, or a branch
permission without branches, means no access to the reports.
"""

from dataclasses import dataclass
from functools import reduce
from operator import or_

from django.db.models import Q

from addresses.models import StockLocation


@dataclass(frozen=True)
class ReportScope:
    user: object
    all: bool = False
    location_ids: tuple = ()
    own: bool = False

    @property
    def level(self):
        if self.all:
            return "global"
        return "location" if self.location_ids else "own"

    def q(self, prefix=""):
        """Q filter for an Order queryset (prefix="order__" for OrderItem)."""
        if self.all:
            return Q()
        parts = []
        if self.location_ids:
            parts.append(Q(**{f"{prefix}location_id__in": self.location_ids}))
        if self.own:
            parts.append(Q(**{f"{prefix}sold_by": self.user}))
        return reduce(or_, parts)

    def can_see_location(self, location_id):
        return self.all or location_id in self.location_ids

    def can_filter_by_seller(self, seller_id):
        """Branch and global viewers can pick any seller (results stay within their
        branches); own-sales viewers can only pick themselves."""
        return self.all or bool(self.location_ids) or seller_id == self.user.pk

    def visible_locations(self):
        qs = StockLocation.objects.all()
        if self.all:
            return qs
        return qs.filter(pk__in=self.location_ids)


def scope_for(user):
    """The ReportScope of ``user`` or None when they cannot see reports."""
    if not (user and user.is_authenticated and user.is_active and user.is_staff):
        return None
    if user.has_perm("analytics.view_global_reports"):
        return ReportScope(user=user, all=True)
    location_ids = ()
    if user.has_perm("analytics.view_location_reports"):
        profile = getattr(user, "staff_profile", None)
        if profile is not None:
            location_ids = tuple(sorted(profile.locations.values_list("pk", flat=True)))
    own = user.has_perm("analytics.view_own_sales")
    if not location_ids and not own:
        return None
    return ReportScope(user=user, location_ids=location_ids, own=own)
