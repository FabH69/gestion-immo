from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve
from django.views.generic import RedirectView

from patrimoine import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("echeances/<int:pk>/document.pdf", views.document_echeance, name="document_echeance"),
    path("etats/loyers-encaisses/", views.etat_loyers_encaisses, name="etat_loyers_encaisses"),
    path("baux/<int:pk>/bail.pdf", views.document_bail, name="document_bail"),
    path("baux/<int:pk>/etat-des-lieux-<str:type_edl>.pdf", views.document_etat_des_lieux_vierge, name="document_edl_vierge"),
    path("baux/<int:pk>/inventaire-<str:type_edl>.pdf", views.document_inventaire, name="document_inventaire"),
    path("depots/<int:pk>/recu.docx", views.recu_depot_mouvement, name="recu_depot_mouvement"),
    path("baux/<int:pk>/recu-depot-garantie.docx", views.recu_depot_bail, name="recu_depot_bail"),
    path("depots/<int:pk>/recu.pdf", views.document_recu_depot, name="document_recu_depot"),
    path("etats-des-lieux/<int:pk>/document.pdf", views.document_etat_des_lieux, name="document_edl"),
    path("etats/endettement/", views.etat_endettement, name="etat_endettement"),
    path("prets/<int:pk>/amortissement.pdf", views.document_amortissement, name="document_amortissement"),
    path("baux/<int:pk>/revision/", views.revision_loyer, name="revision_loyer"),
    path("revisions/<int:pk>/lettre.pdf", views.lettre_revision, name="lettre_revision"),
    path("rappels/<str:type_rappel>/<int:pk>/fait/", views.rappel_fait, name="rappel_fait"),
    path("candidature/<str:jeton>/", views.candidature_publique, name="candidature_publique"),
    path("prive/<path:chemin>", views.fichier_prive, name="fichier_prive"),
    path("reglages/sauvegardes/", views.sauvegardes, name="sauvegardes"),
    path("modeles/champs/", views.liste_champs, name="liste_champs"),
    path("modeles/<int:pk>/bail/<int:bail_pk>/", views.remplir_modele, name="remplir_modele"),
    path("reglages/mails/test/", views.tester_mail, name="tester_mail"),
    path("candidatures/<int:pk>/preparer-mail/", views.preparer_mail_candidature, name="preparer_mail_candidature"),
    path("candidatures/<int:pk>/envoyer-lien/", views.envoyer_lien_candidature, name="envoyer_lien_candidature"),
    path("reglages/sauvegardes/<str:nom>", views.telecharger_sauvegarde, name="telecharger_sauvegarde"),
    path("garants/<int:pk>/acte-de-cautionnement.pdf", views.acte_cautionnement, name="acte_cautionnement"),
    path("lots/<int:pk>/photos.zip", views.photos_zip, name="photos_zip"),
    path("finances/encaissements/", views.suivi_encaissements, name="suivi_encaissements"),
    path("lmnp/", views.biens_lmnp, name="biens_lmnp"),
    path("comptabilite/2072/", views.annexe_2072, name="annexe_2072"),
    path("location-courte-duree/", views.suivi_lcd, name="suivi_lcd"),
    path("simulations/plus-value/", views.plus_value, name="plus_value"),
    path("recherche/", views.recherche, name="recherche"),
    path("balles/", include("commandes.urls")),
    path("", RedirectView.as_view(url="/admin/")),
]

# Photos des logements, servies par Django aussi en ligne (volume faible, usage personnel).
urlpatterns += [re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT})]
