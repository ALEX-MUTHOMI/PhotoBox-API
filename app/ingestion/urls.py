from django.urls import path
from .views import BulkIngestionView

urlpatterns = [
    path('bulk/', BulkIngestionView.as_view(), name='bulk-ingest'),
]
