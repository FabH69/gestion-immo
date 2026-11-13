"""Bouton « Mode test » : passer de la vraie base à la base de test (et revenir), la remettre à zéro."""
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from .. import base_essai


def _cookie(reponse, request, valeur):
    from django.conf import settings

    if valeur:
        reponse.set_cookie(base_essai.COOKIE, valeur, max_age=60 * 60 * 24 * 30, httponly=True, samesite="Lax",
                           secure=not settings.DEBUG)
    else:
        reponse.delete_cookie(base_essai.COOKIE, samesite="Lax")
    return reponse


def _administrateur(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Réservé aux administrateurs.")


@require_POST
@staff_member_required
def essai_entrer(request):
    _administrateur(request)
    base_essai.preparer()
    messages.info(request, "Mode test : tu travailles sur une base de test séparée. Tes vraies données ne sont pas touchées.")
    return _cookie(redirect("admin:index"), request, base_essai.ESSAI)


@require_POST
def essai_sortir(request):
    """Accessible même déconnecté : on enlève seulement le choix de la base de test."""
    return _cookie(redirect("admin:index"), request, None)


@require_POST
@staff_member_required
def essai_reinitialiser(request):
    _administrateur(request)
    if not base_essai.en_essai():
        return redirect("admin:index")
    base_essai.reinitialiser()
    messages.success(request, "Base de test remise à zéro : elle est vierge, comme au premier lancement.")
    return redirect("admin:index")


@require_POST
@staff_member_required
def essai_demo(request):
    _administrateur(request)
    if not base_essai.en_essai():
        return redirect("admin:index")
    base_essai.charger_demo()
    messages.success(request, "Données de démonstration ajoutées dans la base de test.")
    return redirect("admin:index")
