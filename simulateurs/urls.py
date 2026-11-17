from django.urls import path

from . import views
from .registre import disponibles

app_name = "simulateurs"

urlpatterns = [path("", views.accueil, name="accueil")] + [
    path(f"{s.slug}/", getattr(views, s.vue), name=s.slug) for s in disponibles()
]
