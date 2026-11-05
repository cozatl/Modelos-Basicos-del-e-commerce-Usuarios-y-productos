from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.db import models


class UserManager(BaseUserManager):
    def create_user(
        self,
        email,
        full_name=None,
        password=None,
        is_active=True,
        is_staff=False,
        is_superuser=False,
    ):
        if not email:
            raise ValueError("Users should have an email")
        if not password:
            raise ValueError("Users should have a password")
        user = self.model(
            email=self.normalize_email(email.strip().lower()),
            full_name=full_name,
            is_active=is_active,
            is_staff=is_staff,
            is_superuser=is_superuser,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_staffuser(self, email, full_name=None, password=None):
        return self.create_user(
            email, full_name=full_name, password=password, is_staff=True
        )

    def create_superuser(self, email, full_name=None, password=None):
        return self.create_user(
            email,
            full_name=full_name,
            password=password,
            is_staff=True,
            is_superuser=True,
        )


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(max_length=255, unique=True)
    full_name = models.CharField(max_length=255, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    timestamp = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "email"
    # Ask for full name while creating a superuser in console
    REQUIRED_FIELDS = ("full_name",)

    objects = UserManager()

    def __str__(self):
        return self.email

    def get_full_name(self):
        if self.full_name:
            return self.full_name
        return self.email

    def get_short_name(self):
        return self.email

    def save(self, *args, **kwargs):
        if self.is_superuser:
            self.is_staff = True
        super().save(*args, **kwargs)
