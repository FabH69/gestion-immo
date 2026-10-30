"""Génère les échéances d'un mois pour tous les baux actifs (python manage.py generer_echeances [AAAA-MM])."""
from datetime import date

from django.core.management.base import BaseCommand
from django.utils import timezone

from patrimoine.models import Echeance


class Command(BaseCommand):
    help = "Génère les échéances du mois (par défaut le mois en cours)"

    def add_arguments(self, parser):
        parser.add_argument("mois", nargs="?", help="Mois au format AAAA-MM")

    def handle(self, *args, **options):
        if options["mois"]:
            annee, mois = map(int, options["mois"].split("-"))
            periode = date(annee, mois, 1)
        else:
            periode = timezone.localdate().replace(day=1)
        n = Echeance.generer_pour_mois(periode)
        self.stdout.write(self.style.SUCCESS(f"{n} échéance(s) créée(s) pour {periode:%m/%Y}."))
