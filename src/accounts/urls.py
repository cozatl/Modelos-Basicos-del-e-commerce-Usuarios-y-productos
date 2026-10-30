from django.urls import re_path

from accounts.views import (
    LogoutView,
    RegisterView,
    ThrottledTokenObtainPairView,
    ThrottledTokenRefreshView,
    UserProfileView,
)

# Define namespace which is required for the template
app_name = "accounts"

urlpatterns = [
    re_path(r"^api/v1/register/?$", RegisterView.as_view(), name="register"),
    re_path(r"^api/v1/logout/?$", LogoutView.as_view(), name="logout"),
    re_path(
        r"^api/v1/profile/?$", UserProfileView.as_view(), name="user-profile"
    ),
    # Use email and password to get refresh and access token
    re_path(
        r"^api/v1/token/?$",
        ThrottledTokenObtainPairView.as_view(),
        name="user-token",
    ),
    # Use refresh token to generate a new access token
    re_path(
        r"^api/v1/token/refresh/?$",
        ThrottledTokenRefreshView.as_view(),
        name="token-refresh",
    ),
]
