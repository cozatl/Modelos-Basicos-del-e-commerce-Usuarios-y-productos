from django.conf import settings
from django.db.models import Q
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from .choices import AccessType, ProductType
from .models import Category, Product
from .pagination import ProductPagination
from .serializers import (
    CategorySerializer,
    ProductPreviewSerializer,
    ProductSerializer,
)


class PreviewRateThrottle(AnonRateThrottle):
    """Limits the public preview per IP (rate in settings: "preview")."""

    scope = "preview"


class ReadPublicWriteByPermission(permissions.DjangoModelPermissions):
    """Anyone can read; writing needs the model permission (admin groups)."""

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return super().has_permission(request, view)


class CategoryViewSet(viewsets.ModelViewSet):
    """
    Categories. Public read (the home page and the catalog filter use it);
    create/update/delete with products.*_category permissions.
    Not paginated: the list is short and the front builds the menu with it.
    """

    queryset = Category.objects.select_related("parent")
    serializer_class = CategorySerializer
    permission_classes = (ReadPublicWriteByPermission,)
    pagination_class = None
    lookup_field = "slug"

    def get_queryset(self):
        queryset = super().get_queryset()
        if not self.request.user.has_perm("products.change_category"):
            queryset = queryset.filter(is_active=True)
        return queryset


class ProductViewSet(viewsets.ModelViewSet):
    """
    Products API.

    - Read (list/retrieve): any authenticated user.
    - Write (create/update/delete): users whose group has the
      products.add/change/delete_product permission (or superusers).
    - preview/: public, first N featured products for the home page.

    Optional filters: ?type=<product_type>&access=<access_type>
    &category=<slug> (a parent category includes its subcategories)
    """

    queryset = Product.objects.select_related("category")
    serializer_class = ProductSerializer
    # GET only requires authentication; POST/PUT/PATCH/DELETE require
    # the model permission assigned through Django admin groups.
    permission_classes = (permissions.DjangoModelPermissions,)
    pagination_class = ProductPagination
    lookup_field = "slug"

    def get_queryset(self):
        queryset = super().get_queryset()

        # Customers only see active products; catalog managers see all
        if not self.request.user.has_perm("products.change_product"):
            queryset = queryset.filter(is_active=True)

        params = self.request.query_params
        product_type = params.get("type")
        if product_type in ProductType.values:
            queryset = queryset.filter(product_type=product_type)
        access_type = params.get("access")
        if access_type in AccessType.values:
            queryset = queryset.filter(access_type=access_type)

        category = params.get("category")
        if category:
            queryset = queryset.filter(
                Q(category__slug=category) | Q(category__parent__slug=category)
            )

        return queryset

    @action(
        detail=False,
        methods=("get",),
        serializer_class=ProductPreviewSerializer,
        permission_classes=(permissions.AllowAny,),
        # No auth at all: an expired token in the header must not
        # turn the public home page into a 401
        authentication_classes=(),
        pagination_class=None,
        throttle_classes=(PreviewRateThrottle,),
    )
    def preview(self, request):
        """Featured products first, filled with the newest ones."""
        limit = getattr(settings, "PRODUCTS_PREVIEW_LIMIT", 8)
        active = Product.objects.filter(is_active=True).select_related("category")

        products = list(active.filter(is_featured=True)[:limit])
        missing = limit - len(products)
        if missing > 0:
            products += list(active.filter(is_featured=False)[:missing])

        serializer = self.get_serializer(products, many=True)
        return Response(serializer.data)
