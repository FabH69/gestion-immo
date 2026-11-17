"""Liste des simulateurs, dans l'ordre d'affichage.

Ajouter un simulateur :
1. le calcul dans `calculs/<nom>.py` (fonctions pures, testées) ;
2. le formulaire dans `forms.py` et la vue dans `views.py` ;
3. le gabarit `templates/simulateurs/<nom>.html` (qui étend `simulateurs/base.html`) ;
4. une ligne ci-dessous. L'adresse (/simulateurs/<slug>/), la page d'accueil et le menu de l'appli suivent d'eux-mêmes.
Un simulateur sans vue (`vue=None`) s'affiche « bientôt » sur la page d'accueil, sans lien.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Simulateur:
    slug: str
    titre: str
    accroche: str
    emoji: str
    vue: str | None = None  # nom de la fonction dans simulateurs/views.py

    @property
    def disponible(self):
        return self.vue is not None

    @property
    def nom_url(self):
        return f"simulateurs:{self.slug}"


SIMULATEURS = [
    Simulateur("frais-de-notaire", "Frais de notaire",
               "Ancien ou neuf, avec les droits de mutation 2026 de votre département.", "🏛️", "frais_notaire"),
    Simulateur("pret-immobilier", "Mensualités d'un prêt",
               "Mensualité, assurance, coût total, TAEG et tableau d'amortissement.", "🏦", "pret"),
    Simulateur("rentabilite", "Rentabilité locative", "Rendement brut, net et cash-flow d'un investissement.", "📈"),
    Simulateur("capacite-emprunt", "Capacité d'emprunt", "Combien pouvez-vous emprunter avec vos revenus ?", "💶"),
    Simulateur("plus-value", "Plus-value à la revente", "Impôt et prélèvements sociaux selon la durée de détention.", "🏷️"),
]


def disponibles():
    return [s for s in SIMULATEURS if s.disponible]
