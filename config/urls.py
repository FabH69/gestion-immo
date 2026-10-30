from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from patrimoine.views import document_echeance, etat_loyers_encaisses

urlpatterns = [
    path("admin/", admin.site.urls),
    path("echeances/<int:pk>/document.pdf", document_echeance, name="document_echeance"),
    path("etats/loyers-encaisses/", etat_loyers_encaisses, name="etat_loyers_encaisses"),
    path("", RedirectView.as_view(url="/admin/")),
]
