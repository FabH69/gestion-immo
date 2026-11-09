"""Outils partagés par les modèles : stockage privé des pièces, chemins de fichiers, calcul de dates."""
import calendar


def stockage_prive():
    """Pièces d'identité : rangées hors du dossier media public (réglage PRIVE_ROOT), visibles seulement depuis l'administration."""
    import os

    from django.conf import settings
    from django.core.files.storage import FileSystemStorage

    class StockagePrive(FileSystemStorage):
        @property
        def base_location(self):
            return str(settings.PRIVE_ROOT)

        @property
        def location(self):
            return os.path.abspath(self.base_location)

    return StockagePrive(base_url="/prive/")


def _chemin_piece(instance, nom):
    import secrets

    return f"pieces-identite/{secrets.token_hex(8)}-{nom}"


def _ajouter_mois(d, n):
    mois = d.month - 1 + n
    annee, mois = d.year + mois // 12, mois % 12 + 1
    return d.replace(year=annee, month=mois, day=min(d.day, calendar.monthrange(annee, mois)[1]))
