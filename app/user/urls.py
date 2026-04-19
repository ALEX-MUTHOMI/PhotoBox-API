"""
URL mappings for the user API.
"""
from django.urls import path
from user import views

app_name = 'user'

urlpatterns = [
    # ==========================================
    # 1. PUBLIC DOORS (Registration)
    # ==========================================
    path('create/', views.CreateUserView.as_view(), name='create'),

    # ==========================================
    # 2. THE IDENTITY VAULT (Authentication)
    # ==========================================
    # SECURITY: Custom view with Active Threat Logging & XSS Shield
    path('token/', views.EnterpriseTokenObtainPairView.as_view(), name='token'),

    # SECURITY: Custom view that extracts the token from the HttpOnly Cookie
    path('token/refresh/', views.CookieTokenRefreshView.as_view(), name='token_refresh'),

    # SECURITY: Identity Federation (Google) with Account Takeover protection
    path('google/', views.GoogleLoginView.as_view(), name='google_login'),

    # ==========================================
    # 3. THE VIP SECTION (Profile Management)
    # ==========================================
    path('me/', views.ManageUserView.as_view(), name='me'),
]
