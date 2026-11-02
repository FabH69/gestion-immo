from django.urls import path

from . import views

app_name = "balles"

urlpatterns = [
    path("", views.accueil, name="accueil"),
    path("joueurs/", views.joueurs, name="joueurs"),
    path("joueurs/nouveau/", views.joueur_form, name="joueur_creer"),
    path("joueurs/<int:pk>/", views.joueur_form, name="joueur_modifier"),
    path("commandes/nouvelle/", views.commande_form, name="commande_creer"),
    path("commandes/<int:pk>/", views.commande_detail, name="commande_detail"),
    path("commandes/<int:pk>/modifier/", views.commande_form, name="commande_modifier"),
    path("commandes/<int:pk>/statut/", views.commande_statut, name="commande_statut"),
    path("commandes/<int:pk>/messages/", views.commande_messages, name="commande_messages"),
    path("lignes/<int:pk>/", views.ligne_action, name="ligne_action"),
]
