from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Address
from .serializers import AddressSerializer


class AddressViewSet(viewsets.ModelViewSet):
    """
    /addresses/                    GET list · POST create
    /addresses/<id>/               GET · PUT · PATCH · DELETE
    /addresses/<id>/set-default/   POST — mark as the default address

    Only the current user's addresses are visible (others answer 404). Deleting an
    address never affects past orders: they keep their own copy.
    """

    serializer_class = AddressSerializer
    permission_classes = (permissions.IsAuthenticated,)
    pagination_class = None  # a handful per user

    def get_queryset(self):
        return Address.objects.filter(user=self.request.user)

    @action(detail=True, methods=["post"], url_path="set-default")
    def set_default(self, request, pk=None):
        address = self.get_object()
        address.make_default()
        address.refresh_from_db()
        return Response(self.get_serializer(address).data, status=status.HTTP_200_OK)
