from django.urls import path

from .views import AdoptView

app_name = "provisioning_api"

urlpatterns = [
    path("api/provision/adopt/", AdoptView.as_view(), name="adopt"),
]
