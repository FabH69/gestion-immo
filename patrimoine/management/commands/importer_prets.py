"""Importe des prêts depuis un fichier CSV (séparateur « ; », nombres à la française).

    python manage.py importer_prets prets.csv [--appliquer]

Colonnes : emprunteur;bien;banque;reference;type;montant;taux;duree_mois;premiere_echeance;
assurance_mensuelle;taux_assurance;notes. L'emprunteur et le bien sont retrouvés par leur nom.
Un prêt déjà importé (même banque et même n° de prêt) est mis à jour au lieu d'être dupliqué.
"""
import csv
from datetime import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from patrimoine.models import Bien, Pret, Proprietaire


def _decimal(valeur):
    valeur = (valeur or "").strip().replace(" ", "").replace(",", ".")
    return Decimal(valeur) if valeur else None


class Command(BaseCommand):
    help = "Importe des prêts depuis un fichier CSV"

    def add_arguments(self, parser):
        parser.add_argument("fichier")
        parser.add_argument("--appliquer", action="store_true", help="Enregistre réellement les données")

    def handle(self, fichier, appliquer, **options):
        with open(fichier, encoding="utf-8-sig", newline="") as f:
            lignes = [l for l in csv.DictReader(f, delimiter=";") if (l.get("banque") or "").strip()]
        with transaction.atomic():
            for ligne in lignes:
                emprunteur = Proprietaire.objects.filter(nom__iexact=ligne["emprunteur"].strip()).first()
                if not emprunteur:
                    raise CommandError(f"Emprunteur introuvable : {ligne['emprunteur']}")
                bien = Bien.objects.filter(nom__iexact=ligne["bien"].strip()).first() if ligne["bien"].strip() else None
                valeurs = {
                    "proprietaire": emprunteur, "bien": bien,
                    "type": Pret.Type.IN_FINE if "fine" in ligne["type"].lower() else Pret.Type.AMORTISSABLE,
                    "montant": _decimal(ligne["montant"]), "taux": _decimal(ligne["taux"]),
                    "duree_mois": int(ligne["duree_mois"]),
                    "date_premiere_echeance": datetime.strptime(ligne["premiere_echeance"].strip(), "%d/%m/%Y").date(),
                    "assurance_mensuelle": _decimal(ligne["assurance_mensuelle"]) or Decimal(0),
                    "taux_assurance": _decimal(ligne.get("taux_assurance")),
                    "notes": (ligne.get("notes") or "").strip(),
                }
                pret, cree = Pret.objects.update_or_create(banque=ligne["banque"].strip(), reference=ligne["reference"].strip(), defaults=valeurs)
                self.stdout.write(f"  {'créé' if cree else 'mis à jour'} : {pret} – {emprunteur}{' › ' + bien.nom if bien else ''}, "
                                  f"{pret.mensualite_totale} €/mois assurance comprise, jusqu'au {pret.date_derniere_echeance:%d/%m/%Y}")
                if ligne["bien"].strip() and not bien:
                    self.stdout.write(self.style.WARNING(f"    bien « {ligne['bien']} » introuvable : prêt enregistré sans bien"))
            if not appliquer:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("Simulation : rien n'a été enregistré. Ajoutez --appliquer pour importer."))
            else:
                self.stdout.write(self.style.SUCCESS(f"{len(lignes)} prêt(s) enregistré(s)."))
