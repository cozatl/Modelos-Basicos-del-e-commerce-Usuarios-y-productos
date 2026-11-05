from typing import ClassVar

from django.contrib.auth import (
    get_user_model,
    password_validation,
)
from rest_framework import serializers

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    """Complete user management (only for superusers)."""

    password = serializers.CharField(
        write_only=True,
        required=False,
        style={"input_type": "password"},
    )

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "full_name",
            "password",
            "is_active",
            "is_staff",
            "is_superuser",
            "timestamp",
        )
        read_only_fields = ("id", "is_superuser", "timestamp")

    def validate_email(self, value):
        value = value.strip().lower()
        qs = User.objects.filter(email__iexact=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate_password(self, value):
        password_validation.validate_password(value, self.instance)
        return value

    def validate(self, attrs):
        request = self.context.get("request")
        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError({"password": "This field is required."})
        if (
            "password" in attrs
            and self.instance is not None
            and request is not None
            and not request.user.is_superuser
            and self.instance.is_staff
            and self.instance.pk != request.user.pk
        ):
            raise serializers.ValidationError(
                {
                    "password": "Only a superuser can change another "
                    "staff member's password."
                }
            )
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        instance = super().update(instance, validated_data)
        if password:
            instance.set_password(password)
            instance.save(update_fields=["password"])
        return instance


class UserProfileSerializer(UserSerializer):
    current_password = serializers.CharField(
        write_only=True, required=False, style={"input_type": "password"}
    )

    class Meta(UserSerializer.Meta):
        fields = UserSerializer.Meta.fields + ("current_password",)
        read_only_fields = (
            "id",
            "email",
            "is_active",
            "is_staff",
            "is_superuser",
            "timestamp",
        )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        current = attrs.pop("current_password", None)
        if attrs.get("password") and not (
            current and self.instance.check_password(current)
        ):
            raise serializers.ValidationError(
                {"current_password": "Current password is incorrect."}
            )
        return attrs


class RegisterSerializer(serializers.ModelSerializer):
    """Public sign-up. Only email, full name and password are accepted;
    is_staff / is_superuser / is_active can never come from the client."""

    password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )

    class Meta:
        model = User
        fields = ("email", "full_name", "password")
        extra_kwargs: ClassVar[dict] = {
            "full_name": {
                "required": True,
                "allow_null": False,
                "allow_blank": False,
            },
        }

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate_password(self, value):
        password_validation.validate_password(value)
        return value

    def create(self, validated_data):
        # create_user lowercases the email, hashes the password and leaves
        # is_staff=False / is_superuser=False by default.
        return User.objects.create_user(**validated_data)
