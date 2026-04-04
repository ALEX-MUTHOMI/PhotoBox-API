"""
URL mappings for the user API.
"""
from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from user import views

app_name = 'user'

urlpatterns = [
    path('create/', views.CreateUserView.as_view(), name='create'),

    # SECURITY: Pointed to our custom view with Active Threat Logging
    path('token/', views.EnterpriseTokenObtainPairView.as_view(), name='token'),

    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('me/', views.ManageUserView.as_view(), name='me'),
]
