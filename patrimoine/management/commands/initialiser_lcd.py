"""Paramètre un logement en courte durée (frais par séjour et charges mensuelles), sans créer de doublon.

    python manage.py initialiser_lcd "Chasselay" --menage 28 --consommables 2 --charge "Electricité=80" --charge "Eau=29"
"""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from patrimoine.models import ChargeLCD, Lot, ParametresLCD


class Command(BaseCommand):
    help = "Crée les paramètres et charges courte durée d'un lot s'ils n'existent pas encore"

    def add_arguments(self, parser):
        parser.add_argument("lot")
        parser.add_argument("--menage", type=Decimal, default=Decimal(0))
        parser.add_argument("--consommables", type=Decimal, default=Decimal(0))
        parser.add_argument("--conciergerie", type=Decimal, default=Decimal(0), help="% du revenu net")
        parser.add_argument("--charge", action="append", default=[], help="« Libellé=montant mensuel »")

    def handle(self, lot, menage, consommables, conciergerie, charge, **options):
        lots = Lot.objects.filter(designation__iexact=lot)
        if lots.count() != 1:
            raise CommandError(f"Lot « {lot} » introuvable ou ambigu.")
        lot = lots.get()
        if lot.exploitation != "courte":
            lot.exploitation = "courte"
            lot.save(update_fields=["exploitation"])
        _, cree = ParametresLCD.objects.get_or_create(
            lot=lot, defaults={"menage_par_sejour": menage, "consommables_par_sejour": consommables, "commission_gestion": conciergerie})
        nouvelles = 0
        for c in charge:
            libelle, _, montant = c.partition("=")
            _, c_cree = ChargeLCD.objects.get_or_create(lot=lot, libelle=libelle.strip(), defaults={"montant_mensuel": Decimal(montant.replace(",", "."))})
            nouvelles += c_cree
        self.stdout.write(f"  {lot} : paramètres {'créés' if cree else 'déjà présents'}, {nouvelles} charge(s) ajoutée(s)")
