from django.urls import path

from . import views
from .registre import disponibles

app_name = "simulateurs"

urlpatterns = [path("", views.accueil, name="accueil")] + [
    path(f"{s.slug}/", getattr(views, s.vue), name=s.slug) for s in disponibles()
] + [
    # module investisseur : calcul public, enregistrement dans l'espace connecté
    path("rentabilite/calcul/", views.rentabilite_calcul, name="rentabilite_calcul"),
    path("rentabilite/prix-du-marche/", views.rentabilite_dvf, name="rentabilite_dvf"),
    path("rentabilite/mes-simulations/", views.mes_simulations, name="mes_simulations"),
    path("rentabilite/enregistrer/", views.simulation_enregistrer, name="simulation_enregistrer"),
    path("rentabilite/<int:pk>/", views.simulation, name="simulation"),
    path("rentabilite/<int:pk>/dupliquer/", views.simulation_dupliquer, name="simulation_dupliquer"),
    path("rentabilite/<int:pk>/supprimer/", views.simulation_supprimer, name="simulation_supprimer"),
    path("rentabilite/<int:pk>/creer-le-bien/", views.simulation_creer_bien, name="simulation_creer_bien"),
]
