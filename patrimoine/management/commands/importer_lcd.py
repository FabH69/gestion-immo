"""Importe des exports Airbnb ou Booking (CSV ou Excel) pour un logement en courte durée.

    python manage.py importer_lcd "Chasselay" fichier1.csv [fichier2.xlsx ...]
"""
from django.core.management.base import BaseCommand, CommandError

from patrimoine import lcd
from patrimoine.models import Lot


class Command(BaseCommand):
    help = "Importe des réservations Airbnb ou Booking pour un lot"

    def add_arguments(self, parser):
        parser.add_argument("lot", help="Désignation du lot, ex. « Chasselay »")
        parser.add_argument("fichiers", nargs="+")

    def handle(self, lot, fichiers, **options):
        lots = Lot.objects.filter(designation__iexact=lot)
        if lots.count() != 1:
            raise CommandError(f"Lot « {lot} » introuvable ou ambigu.")
        for chemin in fichiers:
            with open(chemin, "rb") as f:
                plateforme, creees, maj, ignorees = lcd.importer(lots.get(), f, chemin)
            self.stdout.write(f"  {chemin} ({plateforme}) : {creees} ajoutée(s), {maj} mise(s) à jour, {ignorees} ignorée(s)")
