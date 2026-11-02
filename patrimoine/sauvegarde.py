"""Sauvegardes : un fichier zip avec la base de données, les photos (media) et les pièces privées.

- une sauvegarde est faite automatiquement au premier passage dans l'application après 24 h sans sauvegarde ;
- les 7 dernières sont gardées sur le serveur (dossier « sauvegardes ») ;
- la page Réglages › Sauvegardes permet d'en créer une et de la télécharger pour la garder chez soi.
"""
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from django.conf import settings

A_GARDER = 7
INTERVALLE = 24 * 3600
PREFIXE = "sauvegarde-gestion-immo-"


def dossier():
    d = Path(getattr(settings, "SAUVEGARDES_ROOT", settings.BASE_DIR / "sauvegardes"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def liste():
    """Sauvegardes présentes sur le serveur, la plus récente d'abord."""
    return sorted(dossier().glob(PREFIXE + "*.zip"), key=lambda f: f.stat().st_mtime, reverse=True)


def _ajouter_dossier(archive, racine, nom):
    racine = Path(racine)
    if racine.is_dir():
        for f in sorted(racine.rglob("*")):
            if f.is_file():
                archive.write(f, f"{nom}/{f.relative_to(racine)}")


def creer():
    """Crée une sauvegarde complète et supprime les plus anciennes au-delà de 7 ; renvoie le chemin du zip."""
    base = f"{PREFIXE}{datetime.now():%Y-%m-%d-%Hh%M-%S}"
    cible, n = dossier() / f"{base}.zip", 1
    while cible.exists():
        n += 1
        cible = dossier() / f"{base}-{n}.zip"
    temporaire = cible.with_suffix(".tmp")
    with tempfile.TemporaryDirectory() as tmp:
        # copie cohérente de la base, même si quelqu'un l'utilise au même moment
        copie = Path(tmp) / "db.sqlite3"
        from django.db import connection

        connection.ensure_connection()
        destination = sqlite3.connect(str(copie))
        with destination:
            connection.connection.backup(destination)  # même connexion que l'appli : pas de blocage
        destination.close()
        with zipfile.ZipFile(temporaire, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(copie, "db.sqlite3")
            _ajouter_dossier(archive, settings.MEDIA_ROOT, "media")
            _ajouter_dossier(archive, settings.PRIVE_ROOT, "prive")
            archive.writestr("LISEZMOI.txt", (
                "Sauvegarde de Gestion immobilière.\n"
                "db.sqlite3 : toutes les données. media : photos. prive : pièces d'identité (confidentiel).\n"
                "Pour restaurer : remplacer db.sqlite3, media et prive dans le dossier gestion-immo, puis recharger l'application.\n"))
    temporaire.rename(cible)
    for ancienne in liste()[A_GARDER:]:
        ancienne.unlink(missing_ok=True)
    return cible


_derniere_verification = 0.0


def si_necessaire():
    """Sauvegarde automatique : au plus une vérification par heure, une sauvegarde si la dernière date de plus de 24 h."""
    global _derniere_verification
    maintenant = time.time()
    if maintenant - _derniere_verification < 3600:
        return None
    _derniere_verification = maintenant
    existantes = liste()
    if existantes and maintenant - existantes[0].stat().st_mtime < INTERVALLE:
        return None
    return creer()


class SauvegardeAutomatiqueMiddleware:
    """Déclenche la sauvegarde automatique quand une personne connectée utilise l'application."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if getattr(settings, "SAUVEGARDE_AUTO", True) and user is not None and user.is_authenticated and user.is_staff:
            try:
                si_necessaire()
            except Exception:  # une sauvegarde ratée ne doit jamais bloquer l'application
                import logging

                logging.getLogger(__name__).exception("Sauvegarde automatique impossible")
        return response
