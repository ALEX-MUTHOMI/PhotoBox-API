from django.urls import path
from .views import PricingPlanListView, GenerateCheckoutLinkView

urlpatterns = [
    path('plans/', PricingPlanListView.as_view(), name='pricing-plans'),
    path('generate/', GenerateCheckoutLinkView.as_view(), name='generate-checkout'),
]
