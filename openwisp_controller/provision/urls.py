from django.urls import path

from . import views

app_name = "provision"

urlpatterns = [
    path("adopt/", views.adopt, name="adopt"),
]
