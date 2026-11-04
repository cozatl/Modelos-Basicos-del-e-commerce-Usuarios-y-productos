import re

from rest_framework import serializers

from .models import Address


class AddressSerializer(serializers.ModelSerializer):
    """The owner always comes from request.user (never from the payload)."""

    state_display = serializers.CharField(source="get_state_display", read_only=True)

    class Meta:
        model = Address
        fields = (
            "id",
            "label",
            "recipient_name",
            "phone",
            "street",
            "exterior_number",
            "interior_number",
            "neighborhood",
            "postal_code",
            "municipality",
            "city",
            "state",
            "state_display",
            "references",
            "is_default",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "state_display", "created_at", "updated_at")

    def to_internal_value(self, data):
        # Accept "55 1234 5678" / "55-1234-5678" and " 01000 ":
        # keep digits only.
        if hasattr(data, "copy"):
            data = data.copy()
        for field in ("phone", "postal_code"):
            value = data.get(field) if hasattr(data, "get") else None
            if isinstance(value, str):
                data[field] = re.sub(r"\D", "", value)
        return super().to_internal_value(data)

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)
