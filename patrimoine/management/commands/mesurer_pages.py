"""Mesure le temps des principales pages sur les vraies données, sans rien modifier :
    python manage.py mesurer_pages
Affiche, pour chaque page, le temps et le nombre de requêtes, puis le temps de chaque contrôle et du menu.
Les pages sont ouvertes avec le premier compte administrateur ; tout est annulé à la fin (aucune écriture gardée)."""
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext

PAGES = [
    ("Accueil", "/admin/"),
    ("À faire", "/a-faire/"),
    ("Contrôles", "/controles/"),
    ("Relances d'impayés", "/impayes/relancer/"),
    ("Compléter avec Excel", "/impayes/completer/"),
    ("Suivi des encaissements", "/finances/encaissements/"),
    ("Loyers encaissés", "/etats/loyers-encaisses/"),
    ("Endettement", "/etats/endettement/"),
    ("Liste des loyers", "/admin/patrimoine/echeance/"),
    ("Paiements reçus", "/admin/patrimoine/versement/"),
    ("Baux", "/admin/patrimoine/bail/"),
    ("Biens", "/admin/patrimoine/bien/"),
    ("Lots", "/admin/patrimoine/lot/"),
    ("Locataires", "/admin/patrimoine/locataire/"),
    ("Factures", "/admin/patrimoine/piececomptable/"),
    ("Prêts", "/admin/patrimoine/pret/"),
    ("Rappels", "/admin/patrimoine/rappel/"),
]


class _Annuler(Exception):
    pass


def _mesurer(fonction):
    with CaptureQueriesContext(connection) as q:
        debut = time.perf_counter()
        resultat = fonction()
        duree = time.perf_counter() - debut
    return duree, len(q), resultat


class Command(BaseCommand):
    help = "Mesure le temps de chargement des principales pages (rien n'est modifié)"

    def handle(self, *args, **options):
        from django.contrib.auth.models import User

        from patrimoine import controles

        admin = User.objects.filter(is_superuser=True, is_active=True).order_by("pk").first()
        if admin is None:
            raise CommandError("Aucun compte administrateur.")
        lignes = []
        try:
            with override_settings(ALLOWED_HOSTS=["*"], SAUVEGARDE_AUTO=False, LOYERS_AUTO=False, DEBUG=False), transaction.atomic():
                client = Client()
                client.force_login(admin)
                client.get("/admin/")  # premier passage : chargement du code, hors mesure
                self.stdout.write("Pages (temps, nombre de requêtes à la base) :")
                for nom, url in PAGES:
                    duree, n, r = _mesurer(lambda: client.get(url))
                    lignes.append((duree, nom))
                    self.stdout.write(f"  {duree:6.2f} s  {n:5d} req.  {nom}" + ("" if r.status_code == 200 else f"  (code {r.status_code})"))
                self.stdout.write("\nContrôles (refaits toutes les 2 minutes pour la pastille du menu) :")
                total = 0
                for code, _, titre, _, _, fonction, _ in controles.CONTROLES:
                    duree, n, res = _mesurer(fonction)
                    total += duree
                    self.stdout.write(f"  {duree:6.2f} s  {n:5d} req.  {titre} ({len(res)} ligne(s))")
                self.stdout.write(f"  {total:6.2f} s  au total")
                raise _Annuler
        except _Annuler:
            pass
        lignes.sort(reverse=True)
        self.stdout.write("\nLes plus lentes : " + ", ".join(f"{nom} ({d:.1f} s)" for d, nom in lignes[:3]))
        self.stdout.write("Copiez tout ce texte et collez-le dans le fil Claude.")
