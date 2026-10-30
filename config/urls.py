from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from patrimoine import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("echeances/<int:pk>/document.pdf", views.document_echeance, name="document_echeance"),
    path("etats/loyers-encaisses/", views.etat_loyers_encaisses, name="etat_loyers_encaisses"),
    path("baux/<int:pk>/bail.pdf", views.document_bail, name="document_bail"),
    path("baux/<int:pk>/etat-des-lieux-<str:type_edl>.pdf", views.document_etat_des_lieux_vierge, name="document_edl_vierge"),
    path("etats-des-lieux/<int:pk>/document.pdf", views.document_etat_des_lieux, name="document_edl"),
    path("", RedirectView.as_view(url="/admin/")),
]
