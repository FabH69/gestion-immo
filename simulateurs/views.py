"""Simulateurs publics : accessibles sans compte (vitrine), sans lecture ni écriture en base.

Les formulaires sont en GET : un résultat se partage par son adresse et se recalcule à chaque chargement.
"""
from django.shortcuts import render

from .calculs import notaire
from .calculs import pret as calcul_pret
from .forms import FraisNotaireForm, PretForm
from .registre import SIMULATEURS


def _page(request, gabarit, slug, **contexte):
    simulateur = next(s for s in SIMULATEURS if s.slug == slug)
    return render(request, gabarit, {"simulateur": simulateur, "simulateurs": SIMULATEURS, **contexte})


def _formulaire(request, classe, champ_declencheur):
    """Formulaire lié si la page a été soumise (présence du champ principal dans l'adresse), sinon vierge."""
    if champ_declencheur in request.GET:
        return classe(request.GET)
    return classe(initial={k: v for k, v in request.GET.items()} or None)


def accueil(request):
    return render(request, "simulateurs/accueil.html", {"simulateurs": SIMULATEURS})


def frais_notaire(request):
    form = _formulaire(request, FraisNotaireForm, "prix")
    resultat = None
    if form.is_bound and form.is_valid():
        d = form.cleaned_data
        resultat = notaire.calculer(d["prix"], d["type_bien"], d["departement"], d["primo_accedant"],
                                    d.get("mobilier") or 0, d["formalites"])
    return _page(request, "simulateurs/frais_notaire.html", "frais-de-notaire", form=form, r=resultat,
                 mise_a_jour=notaire.MISE_A_JOUR,
                 nom_departement=notaire.NOMS_DEPARTEMENTS.get(resultat.departement) if resultat else "")


def pret(request):
    form = _formulaire(request, PretForm, "taux")
    resultat = endettement = None
    if form.is_bound and form.is_valid():
        d = form.cleaned_data
        resultat = calcul_pret.simuler(d["capital"], d["taux"], d["duree_annees"] * 12, d.get("taux_assurance") or 0,
                                     d["mode_assurance"], d.get("frais") or 0)
        endettement = resultat.taux_endettement(d.get("revenus"), d.get("autres_credits"))
    return _page(request, "simulateurs/pret.html", "pret-immobilier", form=form, r=resultat, endettement=endettement,
                 endettement_max=calcul_pret.TAUX_ENDETTEMENT_MAX)

