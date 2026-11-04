from django.urls import path

from . import views

app_name = "balles"

urlpatterns = [
    path("", views.accueil, name="accueil"),
    path("joueurs/", views.joueurs, name="joueurs"),
    path("joueurs/nouveau/", views.joueur_form, name="joueur_creer"),
    path("joueurs/<int:pk>/", views.joueur_form, name="joueur_modifier"),
    path("articles/", views.articles, name="articles"),
    path("articles/nouveau/", views.article_form, name="article_creer"),
    path("articles/<int:pk>/", views.article_form, name="article_modifier"),
    path("commandes/<int:pk>/joueurs/<int:joueur_pk>/prevenu/", views.joueur_prevenu, name="joueur_prevenu"),
    path("commandes/nouvelle/", views.commande_form, name="commande_creer"),
    path("commandes/<int:pk>/", views.commande_detail, name="commande_detail"),
    path("commandes/<int:pk>/modifier/", views.commande_form, name="commande_modifier"),
    path("commandes/<int:pk>/supprimer/", views.commande_supprimer, name="commande_supprimer"),
    path("commandes/<int:pk>/dupliquer/", views.commande_dupliquer, name="commande_dupliquer"),
    path("commandes/<int:pk>/statut/", views.commande_statut, name="commande_statut"),
    path("commandes/<int:pk>/messages/", views.commande_messages, name="commande_messages"),
    path("lignes/<int:pk>/", views.ligne_action, name="ligne_action"),
]
