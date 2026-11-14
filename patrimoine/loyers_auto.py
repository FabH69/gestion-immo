"""Génération automatique des loyers du mois : dès qu'une personne connectée utilise l'application à partir du 1er,
les loyers du mois sont créés (une vérification par jour ; sans doublon, les loyers existants sont conservés)."""
import logging

from django.conf import settings
from django.utils import timezone

_dernier_jour = {}  # par base : la vraie et la base de test


def generer_si_besoin():
    from . import base_essai

    aujourdhui, base = timezone.localdate(), base_essai.active()
    if _dernier_jour.get(base) == aujourdhui:
        return 0
    from .models import Echeance

    n = Echeance.generer_pour_mois(aujourdhui.replace(day=1))
    _dernier_jour[base] = aujourdhui
    return n


class LoyersAutomatiquesMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if getattr(settings, "LOYERS_AUTO", True) and user is not None and user.is_authenticated and user.is_staff:
            try:
                generer_si_besoin()
            except Exception:  # ne doit jamais bloquer l'application
                logging.getLogger(__name__).exception("Génération automatique des loyers impossible")
        return self.get_response(request)
