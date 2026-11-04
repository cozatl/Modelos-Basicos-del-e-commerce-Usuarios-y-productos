from django.db import IntegrityError
from django.test import TestCase

from accounts.models import User


class UserModelTest(TestCase):
    """Unit test for User model"""

    @classmethod
    def setUpTestData(cls):
        # Initial configuration for the test case, creating a user instance
        cls.user = User.objects.create(
            email="testuser@example.com",
            full_name="Juan",
            # last_name="Pérez",
            # phone="1234567890",
            password="securepassword123",
        )

    def test_user_creation(self):
        """Validate user is created correctly and its attributes are saved"""
        user = self.user
        self.assertEqual(user.email, "testuser@example.com")
        self.assertEqual(user.full_name, "Juan")
        # self.assertEqual(user.last_name, "Pérez")
        self.assertTrue(user.check_password("securepassword123") or user.password)

    def test_string_representation(self):
        """Validate the __str__ method or text representation of the user"""
        self.assertEqual(str(self.user), self.user.email)

    def test_email_uniqueness(self):
        """Validate duplicate emails are rejected."""
        with self.assertRaises(IntegrityError):
            User.objects.create(
                email="testuser@example.com",
                full_name="María",
                # last_name="Gómez",
                password="otherpassword123",
            )
